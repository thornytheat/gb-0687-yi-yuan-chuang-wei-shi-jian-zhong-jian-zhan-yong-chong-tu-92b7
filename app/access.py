"""Role / ward scoping for the bed schedule.

The operational rule is:

* the night scheduler must see the *whole* hospital capacity (occupancy,
  conflicts and admittable beds), regardless of any ward filter;
* ward staff may only see patient-level rows that belong to their own ward;
* aggregate totals are always computed from every inpatient segment, so a
  filtered view can never shrink the hospital-wide totals;
* the same scoped view is reused by the HTML pages, the JSON APIs and the
  evidence CSV export.

``scoped_view`` never mutates or filters the stored run: it derives a new
view from the complete result.  That is what keeps the cross-ward totals in
balance with all inpatient episodes.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D

from app.errors import DomainError
from app.modules.m2_mapping.rules import BEDS

ROLE_ALIASES = {
    "scheduler": "scheduler",
    "dispatch": "scheduler",
    "dispatcher": "scheduler",
    "night": "scheduler",
    "nightshift": "scheduler",
    "night_shift": "scheduler",
    "床位调度": "scheduler",
    "夜间调度": "scheduler",
    "夜班调度": "scheduler",
    "调度": "scheduler",
    "调度员": "scheduler",
    "ward": "ward",
    "wardstaff": "ward",
    "ward_staff": "ward",
    "nurse": "ward",
    "病区": "ward",
    "病区人员": "ward",
    "病区护士": "ward",
    "护士": "ward",
    "operator": "operator",
    "admin": "operator",
    "data": "operator",
    "analyst": "operator",
    "运营": "operator",
    "运营数据员": "operator",
    "数据员": "operator",
    "全部": "operator",
    "all": "operator",
}

ROLE_LABELS = {
    "scheduler": "床位调度",
    "ward": "病区人员",
    "operator": "运营数据员",
}

WARD_ALIASES = {
    "w1": "一病区",
    "ward1": "一病区",
    "ward-1": "一病区",
    "ward_1": "一病区",
    "1": "一病区",
    "一": "一病区",
    "一病区": "一病区",
    "病区一": "一病区",
    "w2": "二病区",
    "ward2": "二病区",
    "ward-2": "二病区",
    "ward_2": "二病区",
    "2": "二病区",
    "二": "二病区",
    "二病区": "二病区",
    "病区二": "二病区",
}

# Keys that can identify a patient, directly or indirectly.  In this dataset
# event ids and patient ids share a numbering space and the run watermark
# plus event id can single out a person, so scheduler views drop both.
SCHEDULER_DROP_KEYS = {
    "patient", "patients", "raw", "detail", "id", "source", "key", "event_id",
    "fingerprint", "patient_ids",
}


def resolve_role(role):
    """Return the canonical role name, defaulting to the legacy operator."""
    if role is None or str(role).strip() == "":
        return "operator"
    key = str(role).strip().lower()
    key = key.replace(" ", "").replace("-", "_")
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key]
    # Chinese aliases are case-insensitive anyway; also allow W1/W2 style
    # values to be passed through the generic ``scope`` parameter.
    if key in WARD_ALIASES:
        return "ward"
    raise DomainError("ROLE", "未知角色", 403)


def resolve_ward(ward):
    """Return the canonical Chinese ward name, or ``None`` when not given."""
    if ward is None or str(ward).strip() == "":
        return None
    key = str(ward).strip().lower().replace(" ", "")
    if key in WARD_ALIASES:
        return WARD_ALIASES[key]
    raise DomainError("WARD", "未知病区", 422)


def resolve_scope(role=None, ward=None, scope=None):
    """Accept either ``role``/``ward`` or a combined ``scope`` string."""
    if scope:
        text = str(scope).strip()
        if text.lower() in ("all", "operator", "全部", "运营"):
            role, ward = "operator", None
        elif ":" in text:
            left, right = text.split(":", 1)
            role = role or left
            ward = ward or right
        elif text.lower() in ROLE_ALIASES or text in ROLE_ALIASES:
            role = role or text
        else:
            ward = ward or text
    # A bare ward (``role=W1`` or ``ward=W1``) means ward-level access: patient
    # rows must never widen beyond that ward just because a role was omitted.
    if role is not None and str(role).strip().lower().replace(" ", "") in WARD_ALIASES:
        ward = ward or role
        role = "ward"
    if role is None and ward is not None:
        role = "ward"
    role = resolve_role(role)
    ward = resolve_ward(ward)
    if role == "ward" and ward is None:
        # Never silently return an empty patient list: an unscoped ward user
        # would read that as "this ward has no patients".
        raise DomainError("WARD", "病区人员必须指定病区", 403)
    if role != "ward":
        ward = None
    return role, ward


def request_scope(payload=None):
    """Read the scope from the Flask request and an optional JSON payload.

    Query parameters win over payload, and the payload's ``params`` object is
    accepted as a convenience for ``POST /api/*`` callers.
    """
    try:
        from flask import request
    except ImportError:  # pragma: no cover - Flask is a hard dependency
        request = None
    role = ward = scope = None
    if request is not None:
        role = request.args.get("role")
        ward = request.args.get("ward")
        scope = request.args.get("scope")
        role = role or request.headers.get("X-Role")
        ward = ward or request.headers.get("X-Ward")
    if isinstance(payload, dict):
        role = payload.get("role", role)
        ward = payload.get("ward", ward)
        scope = payload.get("scope", scope)
        params = payload.get("params")
        if isinstance(params, dict):
            role = params.get("role", role)
            ward = params.get("ward", ward)
            scope = params.get("scope", scope)
    return resolve_scope(role, ward, scope)


def _dt(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _dec(value):
    if isinstance(value, D):
        return value
    try:
        return D(str(value))
    except Exception:
        return D(0)


def _fmt(value):
    return str(value)


def _minutes(start, end):
    return D(str((end - start).total_seconds())) / 60


def _row_ward(row):
    """Best-effort ward attribution for any row shape."""
    if not isinstance(row, dict):
        return None
    ward = row.get("ward")
    if ward:
        try:
            return resolve_ward(ward)
        except DomainError:
            return str(ward)
    bed = row.get("bed")
    if bed is not None:
        return BEDS.get(str(bed))
    raw = row.get("raw")
    if isinstance(raw, dict):
        return _row_ward(raw)
    return None


def _observation_end(result):
    params = result.get("params") or {}
    value = params.get("observation_end")
    if value:
        return _dt(value)
    ends = [_dt(item["end"]) for item in result.get("intervals", []) if item.get("end")]
    return max(ends) if ends else None


def _occupied_beds(intervals, observed_at):
    """Beds physically occupied at the observation end.

    Intervals are half-open ``[start, end)``.  An open inpatient segment is
    capped at ``observation_end`` by M3 and therefore still occupies the bed;
    a segment that closes exactly at the observation end frees it.
    """
    occupied = set()
    if observed_at is None:
        return occupied
    for item in intervals:
        bed = item.get("bed")
        if not bed:
            continue
        try:
            start, end = _dt(item["start"]), _dt(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if item.get("open"):
            if start <= observed_at:
                occupied.add(str(bed))
        elif start <= observed_at < end:
            occupied.add(str(bed))
    return occupied


def _compute_reconciliation(intervals, calendar):
    original = defaultdict(lambda: D(0))
    split = defaultdict(lambda: D(0))
    for row in intervals:
        try:
            original[_row_ward(row)] += _minutes(_dt(row["start"]), _dt(row["end"]))
        except (KeyError, TypeError, ValueError):
            continue
    for row in calendar:
        split[_row_ward(row)] += _dec(row.get("minutes", 0))
    wards = sorted(set(original) | set(split), key=lambda x: str(x))
    return [
        {
            "ward": ward,
            "episode_minutes": original[ward],
            "calendar_minutes": split[ward],
            "delta": original[ward] - split[ward],
            "state": "通过" if original[ward] == split[ward] else "差异",
        }
        for ward in wards
    ]


def _global_totals(result):
    intervals = result.get("intervals", []) or []
    conflicts = result.get("conflicts", []) or []
    calendar = result.get("calendar", []) or []
    reconciliation = result.get("reconciliation")
    if not reconciliation:
        reconciliation = _compute_reconciliation(intervals, calendar)

    episode_minutes = D(0)
    for row in intervals:
        try:
            episode_minutes += _minutes(_dt(row["start"]), _dt(row["end"]))
        except (KeyError, TypeError, ValueError):
            continue
    calendar_minutes = sum((_dec(row.get("minutes", 0)) for row in calendar), D(0))
    conflict_minutes = sum((_dec(row.get("minutes", 0)) for row in conflicts), D(0))
    ward_episode_minutes = sum(
        (_dec(row.get("episode_minutes", 0)) for row in reconciliation), D(0)
    )
    ward_calendar_minutes = sum(
        (_dec(row.get("calendar_minutes", 0)) for row in reconciliation), D(0)
    )

    observed_at = _observation_end(result)
    occupied = _occupied_beds(intervals, observed_at)
    total_beds = len(BEDS)
    occupied_known = occupied & set(BEDS)
    available = total_beds - len(occupied_known)

    by_ward = []
    for ward in sorted(set(BEDS.values())):
        ward_beds = {bed for bed, owner in BEDS.items() if owner == ward}
        ward_occupied = occupied_known & ward_beds
        ward_intervals = [x for x in intervals if _row_ward(x) == ward]
        ward_conflicts = [x for x in conflicts if _row_ward(x) == ward]
        by_ward.append(
            {
                "ward": ward,
                "beds": len(ward_beds),
                "total_beds": len(ward_beds),
                "occupied_beds": len(ward_occupied),
                "available_beds": len(ward_beds) - len(ward_occupied),
                "admittable_beds": len(ward_beds) - len(ward_occupied),
                "intervals": len(ward_intervals),
                "conflict_count": len(ward_conflicts),
                "conflict_minutes": _fmt(
                    sum((_dec(x.get("minutes", 0)) for x in ward_conflicts), D(0))
                ),
            }
        )

    reconciled = (
        episode_minutes == calendar_minutes
        and ward_episode_minutes == episode_minutes
        and ward_calendar_minutes == calendar_minutes
        and all(_dec(row.get("delta", 0)) == 0 for row in reconciliation)
    )

    return {
        "total_beds": total_beds,
        "beds": total_beds,
        "input_count": result.get("input_count", 0),
        "records": len(result.get("records", []) or []),
        "quarantine": len(result.get("quarantine", []) or []),
        "occupied_beds": len(occupied_known),
        "available_beds": available,
        "admittable_beds": available,
        "intervals": len(intervals),
        "episode_minutes": _fmt(episode_minutes),
        "occupancy_minutes": _fmt(episode_minutes),
        "calendar_minutes": _fmt(calendar_minutes),
        "delta": _fmt(episode_minutes - calendar_minutes),
        "conflict_count": len(conflicts),
        "conflicts": len(conflicts),
        "conflict_minutes": _fmt(conflict_minutes),
        "conflict_beds": len({x.get("bed") for x in conflicts}),
        "wards": len(by_ward),
        "reconciled": reconciled,
        "reconciliation": [_serialize_reconciliation(row) for row in reconciliation],
        "by_ward": by_ward,
    }


def _serialize_reconciliation(row):
    return {
        "ward": row.get("ward"),
        "episode_minutes": _fmt(_dec(row.get("episode_minutes", 0))),
        "calendar_minutes": _fmt(_dec(row.get("calendar_minutes", 0))),
        "delta": _fmt(_dec(row.get("delta", 0))),
        "state": row.get("state", "通过" if _dec(row.get("delta", 0)) == 0 else "差异"),
    }


def _sanitize_scheduler(value):
    if isinstance(value, dict):
        return {
            key: _sanitize_scheduler(item)
            for key, item in value.items()
            if key not in SCHEDULER_DROP_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_scheduler(item) for item in value]
    return value


def _scheduler_audit(audit):
    """Keep the watermark (needed for late detection) but hide identifiers."""
    if not audit:
        return {}
    out = _sanitize_scheduler({k: v for k, v in audit.items() if k != "watermark"})
    out["watermark"] = audit.get("watermark")
    return out


def _filter_rows(rows, ward, patient_ids=None):
    out = []
    for row in rows:
        row_ward = _row_ward(row)
        if row_ward is not None:
            if row_ward == ward:
                out.append(row)
        elif patient_ids is not None and row.get("patient") in patient_ids:
            out.append(row)
    return out


def _filter_stages(stages, ward, patient_ids):
    result = []
    for stage in stages or []:
        item = dict(stage)
        item["rows"] = _filter_rows(stage.get("rows", []), ward, patient_ids)
        result.append(item)
    return result


def _scope_sample(sample, role, ward):
    """Never show other patients' rows in the M1 upload textarea.

    The sample is patient data, so it follows exactly the same rules as the
    result rows: operators see it all, ward staff only their ward, and the
    scheduler gets no patient rows at all.
    """
    if role == "operator":
        return sample
    import csv
    import io

    reader = csv.reader(io.StringIO(sample))
    try:
        header = next(reader)
    except StopIteration:
        return sample
    if role == "scheduler":
        writer = io.StringIO()
        csv.writer(writer, lineterminator="\n").writerow(header)
        return writer.getvalue()
    try:
        ward_index = [h.strip() for h in header].index("ward")
    except ValueError:
        ward_index = None
    kept = [header]
    for cells in reader:
        if not cells:
            continue
        value = cells[ward_index] if ward_index is not None and ward_index < len(cells) else None
        if value is None:
            value = next(
                (c for c in cells if str(c).strip().lower() in WARD_ALIASES),
                None,
            )
        if value is not None and WARD_ALIASES.get(str(value).strip().lower()) == ward:
            kept.append(cells)
    writer = io.StringIO()
    csv.writer(writer, lineterminator="\n").writerows(kept)
    return writer.getvalue()


def scoped_view(result, role=None, ward=None, scope=None):
    """Return a role/ward-scoped copy of a complete run result.

    The stored run is left untouched.  Aggregate capacity, conflict and
    reconciliation totals are computed from every interval/conflict in the
    run, then patient-level rows are filtered (or stripped) for the caller.
    """
    role, ward = resolve_scope(role, ward, scope)
    # Totals are always the hospital-wide figures.  Reuse them when the result
    # was already scoped so a second scope application cannot shrink them.
    total = result.get("totals") or _global_totals(result)
    all_intervals = result.get("intervals", []) or []
    all_records = result.get("records", []) or []
    all_conflicts = result.get("conflicts", []) or []
    all_calendar = result.get("calendar", []) or []
    all_quarantine = result.get("quarantine", []) or []
    all_audit = result.get("audit") or {}
    all_reconciliation = result.get("reconciliation") or total["reconciliation"]

    if role == "operator":
        intervals = all_intervals
        records = all_records
        conflicts = all_conflicts
        calendar = all_calendar
        quarantine = all_quarantine
        audit = all_audit
        reconciliation = all_reconciliation
        stages = result.get("stages", [])
        patient_detail = True
    elif role == "scheduler":
        # The scheduler gets all bed occupancy and conflict aggregates, but
        # no patient/event identifiers at all.
        intervals = _sanitize_scheduler(all_intervals)
        records = _sanitize_scheduler(all_records)
        conflicts = _sanitize_scheduler(all_conflicts)
        calendar = all_calendar
        quarantine = _sanitize_scheduler(all_quarantine)
        audit = _scheduler_audit(all_audit)
        reconciliation = all_reconciliation
        stages = _sanitize_scheduler(result.get("stages", []))
        patient_detail = False
    else:  # ward
        patient_ids = {
            x.get("patient")
            for x in all_intervals
            if x.get("patient") and _row_ward(x) == ward
        } | {
            x.get("patient")
            for x in all_records
            if x.get("patient") and _row_ward(x) == ward
        }
        intervals = _filter_rows(all_intervals, ward)
        records = _filter_rows(all_records, ward)
        conflicts = _filter_rows(all_conflicts, ward)
        calendar = _filter_rows(all_calendar, ward)
        quarantine = _filter_rows(all_quarantine, ward)
        # Reconciliation is ward-level aggregate, never patient-level: keep the
        # full two-ward balance so ward staff can still see the hospital total
        # reconciling against every inpatient segment.
        reconciliation = all_reconciliation
        stages = _filter_stages(result.get("stages", []), ward, patient_ids)
        audit = dict(all_audit)
        audit["late"] = _filter_rows(all_audit.get("late", []), ward, patient_ids)
        patient_detail = ward is not None

    out = dict(result)
    out["scope"] = {
        "role": role,
        "label": ROLE_LABELS[role],
        "ward": ward,
        "patient_detail": patient_detail,
        "aggregate_only": role == "scheduler",
    }
    out["role"] = role
    out["role_label"] = ROLE_LABELS[role]
    out["ward"] = ward
    out["totals"] = total
    out["capacity"] = {
        "total_beds": total["total_beds"],
        "occupied_beds": total["occupied_beds"],
        "available_beds": total["available_beds"],
        "admittable_beds": total["admittable_beds"],
        "by_ward": total["by_ward"],
    }
    # Convenient top-level aliases for callers that only need capacity.
    out["total_beds"] = total["total_beds"]
    out["occupied_beds"] = total["occupied_beds"]
    out["available_beds"] = total["available_beds"]
    out["admittable_beds"] = total["admittable_beds"]
    out["occupancy_minutes"] = total["occupancy_minutes"]
    out["conflict_count"] = total["conflict_count"]
    out["conflict_minutes"] = total["conflict_minutes"]

    out["intervals"] = intervals
    out["records"] = records
    out["conflicts"] = conflicts
    out["calendar"] = calendar
    out["quarantine"] = quarantine
    out["audit"] = audit
    out["reconciliation"] = reconciliation
    out["stages"] = stages
    out["record_count"] = len(all_records)
    out["late_count"] = len(all_audit.get("late", []))
    return out


def export_rows(result):
    """Rows for the evidence CSV, mirroring exactly the scoped view.

    The export reuses the same scoped ``records``/``quarantine`` lists the page
    and API show, so a role never gains patient detail through the export that
    it cannot see on screen.  Scheduler rows have no identifiers at all because
    ``scoped_view`` already stripped them.
    """
    rows = []
    for row in result.get("records", []):
        rows.append(
            {
                "line": row.get("_line", ""),
                "key": row.get("id", row.get("event_id", "")),
                "code": "PASS",
                "evidence": row,
            }
        )
    for row in result.get("quarantine", []):
        rows.append(
            {
                "line": row.get("line", ""),
                "key": row.get("key", ""),
                "code": row.get("code", ""),
                "evidence": row,
            }
        )
    return rows


def scoped_sample(sample, role, ward):
    """Public helper used by the page view for the M1 textarea."""
    return _scope_sample(sample, resolve_role(role), resolve_ward(ward))


def scoped_run(payload=None, stop=7, persist=False):
    """Run the pipeline using the scope carried by the current request.

    Module handlers call this so every ``/api/mN`` endpoint applies the same
    role/ward rules as the pages and the CSV export.
    """
    from app.pipeline import run

    payload = payload if isinstance(payload, dict) else {}
    role, ward = request_scope(payload)
    return run(payload.get("text"), payload.get("params"), stop=stop,
               persist=persist, role=role, ward=ward)


def scoped_result(result, payload=None):
    """Apply request scope to an already-loaded result (stored runs)."""
    role, ward = request_scope(payload)
    return scoped_view(result, role=role, ward=ward)
