from flask import render_template, request
from datetime import datetime

from app import store
from app.pipeline import run
from app.access import resolve_scope, scoped_result, scoped_sample, ROLE_LABELS
from app.config import TITLE, MODULES, STAGES, COLUMNS, SAMPLES, DEFAULTS


def _request_scope():
    """Resolve the viewer role/ward from query parameters.

    Defaults to the unrestricted operator view so the existing single-user
    workflow is unchanged; ward staff pass ``ward=W1`` (or ``role=ward&ward=W1``)
    and schedulers pass ``role=scheduler``.
    """
    return resolve_scope(
        request.args.get("role") or request.headers.get("X-Role"),
        request.args.get("ward") or request.headers.get("X-Ward"),
        request.args.get("scope"),
    )


def _tracks(items, bounds):
    """Build timeline tracks against a stable (hospital-wide) time axis."""
    start = min((datetime.fromisoformat(x["start"]).timestamp() for x in bounds), default=0)
    end = max((datetime.fromisoformat(x["end"]).timestamp() for x in bounds), default=1)
    span = (end - start) or 1
    tracks = []
    for bed in sorted({x["bed"] for x in items}):
        bars = []
        for x in items:
            if x["bed"] == bed:
                left = (datetime.fromisoformat(x["start"]).timestamp() - start) / span * 100
                width = (
                    datetime.fromisoformat(x["end"]).timestamp()
                    - datetime.fromisoformat(x["start"]).timestamp()
                ) / span * 100
                bars.append({**x, "left": left, "width": width})
        tracks.append({"bed": bed, "bars": bars})
    return tracks


def _scope_links(result):
    """Preset links that keep the current run and switch viewer scope."""
    run_id = result["manifest"]["run_id"]
    stage = request.view_args.get("stage", 4)
    current_ward = result.get("ward")
    links = [
        {
            "key": "operator",
            "label": "运营数据员 · 全量",
            "href": f"/m/{stage}?run={run_id}&role=operator",
            "active": result.get("role") == "operator",
        },
        {
            "key": "scheduler",
            "label": "床位调度 · 全量容量",
            "href": f"/m/{stage}?run={run_id}&role=scheduler",
            "active": result.get("role") == "scheduler",
        },
    ]
    for ward in ("一病区", "二病区"):
        links.append(
            {
                "key": ward,
                "label": f"病区人员 · {ward}",
                "href": f"/m/{stage}?run={run_id}&role=ward&ward={ward}",
                "active": result.get("role") == "ward" and current_ward == ward,
            }
        )
    return links


def view(stage):
    if not 1 <= stage <= 7:
        return "页面不存在", 404
    run_id = request.args.get("run")
    result = store.fetch(run_id) if run_id else run()
    hospital_intervals = result["intervals"]
    role, ward = _request_scope()
    result = scoped_result(result, {"role": role, "ward": ward})
    sample = (SAMPLES / "dirty.csv").read_text(encoding="utf-8-sig")
    sample = scoped_sample(sample, role, ward)
    data = result["stages"][stage - 1]
    tracks = _tracks(result["intervals"], hospital_intervals)
    return render_template(
        f"m{stage}.html",
        title=TITLE,
        modules=MODULES,
        stage=stage,
        result=result,
        data=data,
        sample=sample,
        defaults=DEFAULTS,
        columns=COLUMNS,
        history=store.history(),
        tracks=tracks,
        scope_links=_scope_links(result),
        role_labels=ROLE_LABELS,
    )
