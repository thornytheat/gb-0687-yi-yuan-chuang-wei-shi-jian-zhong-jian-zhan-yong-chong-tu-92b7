# 病床事件定义

id为事件号、patient为患者号、bed为床号，全部保留字符串及前导零。ward支持W1/W2或中文病区名；床号001–004属于一病区，101–104属于二病区。
time需带偏移，统一UTC。type支持admit、transfer、discharge。同一患者按事件时间排序，同刻先出院、再转床、再入院。
占床段[start,end)，终点不重复计数。缺出院延长至observation_end并标记open。转床关闭前床并打开后床。没有入院的出院进入隔离。
同事件最高version采信；同版相互矛盾则全部隔离。完全重复和旧版单列。双占通过区间交集计算分钟，不直接删除来源事件。
当地日历固定+08:00，午夜切分，分钟为Decimal。分段合计应等于日历合计，双占另外列明。迟到基于文件原始行序比较既有最大事件时间，晚到仍完整回放。
复核版本递增，旧版更新409。输入文件与参数纳入运行ID，内容不变重跑不增行。空值不可作为零时长。

## 角色与病区范围

查看范围由 `role` 与 `ward` 决定，只裁剪患者明细，不参与运行ID与计算：合计始终由全部住院段、全部冲突与两病区对账得出。

- `role=operator`（默认）：全部行。
- `role=scheduler`：保留全部床位占用段、双占区间、日历、对账与可收治床数；去除 `patient`、`id`、`source`、`key` 等可定位患者的字段。
- `role=ward` 且 `ward` 为一病区/二病区：仅本区 `records`、`intervals`、`conflicts`、`calendar`、`quarantine`、迟到记录；`totals` 与 `reconciliation` 仍为全量；缺病区返回403。

容量字段：`total_beds` 固定为8；`occupied_beds` 为观测终点仍在占的床位，开放段计为在占、恰在终点结束的段不计；`admittable_beds` = `total_beds − occupied_beds`。`by_ward` 给出每区床位、在占、可收治与冲突。`totals.reconciled` 要求两病区分段分钟合计 = 日历分钟合计 = 全部住院段分钟，且各病区 `delta` 为0。
