# 医院床位事件重建与占用冲突

使用者：医院运营数据员。合并入出转床事件，计算病区每日占床时长并定位双占。

核心规则：按患者事件时间构造半开占床区间，转床同刻先出后入，缺失出院保留开放段，重叠转移隔离。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
docker compose config --quiet
```

浏览器访问 http://localhost:18201 。首页已装载脏样例，可编辑CSV与参数后运行，也可上传文件。七个导航分别对应业务步骤。

## 模块

- M1 事件接收：接收带行号事件，入口 `/m/1`、`GET/POST /api/m1`。
- M2 病区映射：匹配床位病区与时区，入口 `/m/2`、`GET/POST /api/m2`。
- M3 住院分段：回放入出转院事件，入口 `/m/3`、`GET/POST /api/m3`。
- M4 转床裁决：定位床位双占交集，入口 `/m/4`、`GET/POST /api/m4`。
- M5 占床日历：按当地午夜切分占床，入口 `/m/5`、`GET/POST /api/m5`。
- M6 病区对账：核对分段与日历分钟，入口 `/m/6`、`GET/POST /api/m6`。
- M7 补传重算：标记迟到并复核补传，入口 `/m/7`、`GET/POST /api/m7`。

## 演示与复核

1. 运行默认样例，核查隔离原因与业务合计。
2. 修改CSV单元格或参数并重跑，保留行号与输入摘要。
3. 在最后一步复核，旧版本提交返回409。
4. 证据CSV包含采信行和隔离行，重跑相同内容复用批次。

## 角色与病区范围

同一批次按查看者角色裁剪患者明细，但容量、冲突与对账合计始终按全部住院段计算，不随病区筛选缩小。页面顶部与 `GET /api/mN`、`GET /api/runs/<id>`、`GET /api/evidence.csv` 使用同一套范围规则：

| 角色 | 参数 | 可见内容 |
|---|---|---|
| 运营数据员 | 默认 / `role=operator` | 全部患者的采信、隔离、分段、日历与对账明细 |
| 床位调度 | `role=scheduler` | 全院床位占用、双占区间与分钟、可收治床数、两病区对账；不返回患者号、事件号等可定位患者的字段 |
| 病区人员 | `role=ward&ward=W1`（或 `ward=W1`、`ward=二病区`） | 仅本区患者记录；全院合计与两病区对账仍为全量 |

示例如下，三种入口返回同一范围：

```sh
curl 'http://localhost:18201/api/m4?role=scheduler'
curl 'http://localhost:18201/api/m4?role=ward&ward=W1'
curl 'http://localhost:18201/api/evidence.csv?role=ward&ward=W1'
```

- 可收治床位 = 8 − 观测终点仍在占的床位数；开放住院段计为在占，恰好于观测终点结束的段释放床位。
- 合计字段位于 `totals`：`total_beds`、`occupied_beds`、`admittable_beds`（同 `available_beds`）、`intervals`、`conflict_count`、`conflict_minutes`、`episode_minutes`、`calendar_minutes`、`reconciled` 与按病区拆分 `by_ward`。
- `reconciled` 为真表示两病区分段分钟合计与日历分钟合计相等、且与全部住院段对账无差；病区筛选不会改变该结果。
- 角色/病区参数只影响可见范围，不计入批次 ID；相同输入与参数仍复用同一批次。病区人员未指定病区返回 403，避免把空列表误读为"本区无患者"。

## 数据与目录

`app/modules/` 按业务切分；`sample_data/` 为UTF-8 BOM、GB18030及干净/脏样例；`tests/` 含规则与端到端测试。SQLite保存运行与复核，命名卷records保留重启后的记录。若需从头开始，可先停止服务并在确认不再需要记录后删除该项目records卷。

## 定义与边界

床位目录固定为两病区各四床，时间使用明确偏移。未做医嘱、收费与患者隐私字段；患者号仅按查看者角色与病区展示。双占保留证据待人工复核，不自行删除患者。

详细字段、计算定义与原因码见 `docs/DATA-DICTIONARY.md`，回归证据见 `docs/DEPTH.md`。
