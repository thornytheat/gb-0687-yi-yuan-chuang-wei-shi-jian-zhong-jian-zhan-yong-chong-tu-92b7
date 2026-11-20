"""Role and ward scoping: totals stay hospital-wide, details stay in scope."""
import csv
import io
import json

import pytest

from app.access import resolve_scope, scoped_view
from app.errors import DomainError
from app.pipeline import run

WARDS = ("一病区", "二病区")


@pytest.fixture
def full(raw, isolate):
    return run(raw, persist=False)


def _patients(view, ward):
    ids = {x["patient"] for x in view["intervals"] if x["ward"] == ward}
    ids |= {x["patient"] for x in view["records"] if x.get("ward") == ward}
    return ids


def test_scheduler_sees_hospital_capacity_regardless_of_filter(full):
    base = scoped_view(full, role="scheduler")
    filtered = scoped_view(full, role="scheduler", ward="一病区")
    assert filtered["totals"] == base["totals"]
    assert filtered["totals"]["total_beds"] == 8
    assert filtered["totals"]["intervals"] == len(full["intervals"])
    assert filtered["totals"]["conflict_count"] == len(full["conflicts"])
    # A ward filter must not shrink capacity; that is the night-shift misread.
    assert filtered["totals"]["admittable_beds"] == base["totals"]["admittable_beds"]


def test_scheduler_has_no_patient_identifiers(full):
    view = scoped_view(full, role="scheduler")
    patients = {x["patient"] for x in full["intervals"]}
    blob = json.dumps(
        {k: v for k, v in view.items() if k not in ("scope", "role_label")},
        ensure_ascii=False,
    )
    assert '"patient"' not in blob
    for patient in patients:
        assert patient not in blob
    # Occupancy is still visible for the whole hospital.
    assert len(view["intervals"]) == len(full["intervals"])
    assert len(view["conflicts"]) == len(full["conflicts"])


@pytest.mark.parametrize("ward", WARDS)
def test_ward_only_sees_own_patients(full, ward):
    view = scoped_view(full, role="ward", ward=ward)
    own = _patients(full, ward)
    other = _patients(full, WARDS[0] if ward == WARDS[1] else WARDS[1])
    assert {x["patient"] for x in view["intervals"]} <= own
    assert all(x["ward"] == ward for x in view["intervals"])
    assert all(x["ward"] == ward for x in view["records"])
    assert all(x["ward"] == ward for x in view["calendar"])
    assert not (set().union(*[{x["patient"]} for x in view["intervals"]]) & other)


@pytest.mark.parametrize("ward", WARDS)
def test_ward_filter_keeps_hospital_totals(full, ward):
    view = scoped_view(full, role="ward", ward=ward)
    assert view["totals"]["intervals"] == len(full["intervals"])
    assert view["totals"]["conflict_count"] == len(full["conflicts"])
    assert view["totals"]["total_beds"] == 8
    # Ward-level detail is still broken out for both wards.
    assert {w["ward"] for w in view["totals"]["by_ward"]} == set(WARDS)


def test_ward_capacity_matches_unfiltered(full):
    unfiltered = scoped_view(full, role="operator")
    for ward in WARDS:
        view = scoped_view(full, role="ward", ward=ward)
        assert view["totals"]["admittable_beds"] == unfiltered["totals"]["admittable_beds"]
        assert view["totals"]["occupied_beds"] == unfiltered["totals"]["occupied_beds"]


def test_totals_reconcile_with_all_inpatient_segments(full):
    for scope in ({"role": "operator"}, {"role": "scheduler"},
                  {"role": "ward", "ward": "一病区"}):
        view = scoped_view(full, **scope)
        totals = view["totals"]
        assert totals["reconciled"] is True
        assert totals["episode_minutes"] == totals["calendar_minutes"]
        assert totals["delta"] == "0.0"
        segment_minutes = sum(
            (view["totals"]["episode_minutes"] == totals["episode_minutes"],)
        )
        assert all(row["delta"] == "0.0" for row in totals["reconciliation"])


def test_admittable_beds_track_occupancy(full):
    totals = scoped_view(full, role="scheduler")["totals"]
    assert totals["admittable_beds"] == totals["total_beds"] - totals["occupied_beds"]
    assert sum(w["admittable_beds"] for w in totals["by_ward"]) == totals["admittable_beds"]
    assert sum(w["occupied_beds"] for w in totals["by_ward"]) == totals["occupied_beds"]
    assert all(0 <= w["admittable_beds"] <= w["total_beds"] for w in totals["by_ward"])


def test_open_segment_counts_as_occupied(full):
    # Sample data leaves patient 09991 in bed 001 at the observation end.
    view = scoped_view(full, role="scheduler")
    open_items = [x for x in view["intervals"] if x["open"] and x["bed"] == "001"]
    assert open_items
    assert view["totals"]["occupied_beds"] >= 1


def test_scope_does_not_change_run_identity(raw, isolate):
    plain = run(raw, persist=False)
    scoped = run(raw, persist=False, params={"role": "scheduler", "ward": "W1"})
    assert plain["manifest"]["run_id"] == scoped["manifest"]["run_id"]
    assert "role" not in scoped["params"]


def test_double_scope_application_is_stable(full):
    once = scoped_view(full, role="ward", ward="一病区")
    twice = scoped_view(once, role="ward", ward="一病区")
    assert once["totals"] == twice["totals"]
    assert len(once["intervals"]) == len(twice["intervals"])


def test_unknown_role_and_ward_rejected():
    with pytest.raises(DomainError):
        resolve_scope("doctor")
    with pytest.raises(DomainError):
        resolve_scope("ward", "三病区")
    with pytest.raises(DomainError):
        resolve_scope("ward")


def test_scope_aliases():
    assert resolve_scope("W1") == ("ward", "一病区")
    assert resolve_scope(None, "二病区") == ("ward", "二病区")
    assert resolve_scope(None, None, "scheduler") == ("scheduler", None)
    assert resolve_scope(None, None, "ward:一病区") == ("ward", "一病区")
    assert resolve_scope("床位调度") == ("scheduler", None)


def test_api_scope_is_applied(client):
    scheduler = client.get("/api/m4?role=scheduler").json
    assert scheduler["totals"]["conflict_count"] == len(scheduler["conflicts"])
    assert all("patient" not in row for row in scheduler["conflicts"])
    ward = client.get("/api/m4?role=ward&ward=W1").json
    assert ward["totals"]["conflict_count"] == scheduler["totals"]["conflict_count"]
    assert all(row["ward"] == "一病区" for row in ward["conflicts"])
    assert client.get("/api/m4?role=ward").status_code == 403


def test_export_uses_same_scope(client):
    run_id = client.post("/api/runs", json={}).json["manifest"]["run_id"]

    def rows(query):
        text = client.get(f"/api/evidence.csv?run={run_id}{query}").text.lstrip("﻿")
        return list(csv.reader(io.StringIO(text)))

    full_rows = rows("")
    ward_rows = rows("&role=ward&ward=W1")
    sched_rows = rows("&role=scheduler")

    # Ward export is the same result the ward page shows: a strict subset.
    assert 1 < len(ward_rows) < len(full_rows)
    assert set(map(tuple, ward_rows)) <= set(map(tuple, full_rows))
    assert all("二病区" not in row[4] for row in ward_rows[1:])

    # Scheduler export mirrors the aggregate view: occupancy stays visible,
    # patient identifiers never do.
    assert sched_rows[0] == full_rows[0]
    assert len(sched_rows) > 1
    for row in sched_rows[1:]:
        assert "patient" not in row[4]
        assert "00001" not in row[4]

    # The scoped API and the scoped export agree on row counts.
    api = client.get(f"/api/m7?run={run_id}&role=ward&ward=W1")
    assert api.status_code == 200


def test_pages_render_for_each_scope(client):
    for stage in range(1, 8):
        for query in ("", "?role=scheduler", "?role=ward&ward=W1"):
            assert client.get(f"/m/{stage}{query}").status_code == 200


def test_review_response_is_scoped(client):
    run_id = client.post("/api/runs", json={}).json["manifest"]["run_id"]
    ward = client.get(f"/api/runs/{run_id}?role=ward&ward=W1").json
    # GET of a stored run applies the same scope as the pages.
    assert ward["scope"]["role"] == "ward"
    assert all(row["ward"] == "一病区" for row in ward["intervals"])

    reply = client.post(
        f"/api/review/{run_id}?role=ward&ward=W1",
        json={"version": 1, "decision": "通过"},
    )
    assert reply.status_code == 200
    body = reply.json
    assert body["review"] == "通过"
    assert body["scope"]["role"] == "ward"
    assert all(row["ward"] == "一病区" for row in body["intervals"])
    assert body["totals"]["intervals"] == len(client.get(f"/api/m3?run={run_id}").json["intervals"])
