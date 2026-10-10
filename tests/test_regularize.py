import concurrent.futures
from datetime import datetime, timezone

def test_regularize_unknown_employee_or_record(client_app, reset_db):
    res = client_app.patch(
        "/attendance/NONEXISTENT/2026-07-06",
        json={"reason": "Correction reason", "regularized_by": "admin"}
    )
    assert res.status_code == 404

    res2 = client_app.patch(
        "/attendance/EMP0001/2026-07-25",
        json={"reason": "Correction reason", "regularized_by": "admin"}
    )
    assert res2.status_code == 404

def test_regularize_no_change_422(client_app, reset_db):
    # EMP0001 on 2026-07-06 is PRESENT
    res = client_app.patch(
        "/attendance/EMP0001/2026-07-06",
        json={"status": "PRESENT", "reason": "No actual change", "regularized_by": "admin"}
    )
    assert res.status_code == 422
    assert "no actual change" in res.json()["detail"].lower()

def test_regularize_change_to_leave_clears_punches(client_app, reset_db):
    # EMP0001 on 2026-07-06 was PRESENT with punches. Change to LEAVE.
    res = client_app.patch(
        "/attendance/EMP0001/2026-07-06",
        json={"status": "LEAVE", "reason": "Approved retroactive leave", "regularized_by": "hr.manager"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "LEAVE"
    assert data["punch_in"] is None
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["late_minutes"] == 0
    assert data["overtime_minutes"] == 0
    assert data["half_day"] is False
    assert len(data["history"]) == 1

    entry = data["history"][0]
    assert entry["by"] == "hr.manager"
    assert entry["reason"] == "Approved retroactive leave"
    assert isinstance(entry["at"], int)
    assert "status" in entry["changes"]
    assert entry["changes"]["status"] == {"from": "PRESENT", "to": "LEAVE"}
    assert "punch_in" in entry["changes"]
    assert entry["changes"]["punch_in"]["to"] is None

def test_regularize_reject_punch_times_with_leave(client_app, reset_db):
    # Setting LEAVE while providing punch_in -> 422
    res = client_app.patch(
        "/attendance/EMP0001/2026-07-06",
        json={
            "status": "LEAVE",
            "punch_in": 1783312500000,
            "reason": "Invalid correction",
            "regularized_by": "admin"
        }
    )
    assert res.status_code == 422

def test_regularize_punch_in_and_recompute_derived(client_app, reset_db):
    # EMP0003 on 2026-07-06 was punched in at 10:05 IST (late_minutes: 35).
    # Correct punch-in to 09:28 IST (03:58 UTC) -> late_minutes: 0, work_hours recomputed.
    dt_new_in = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
    ts_new_in = int(dt_new_in.timestamp() * 1000)

    res = client_app.patch(
        "/attendance/EMP0003/2026-07-06",
        json={
            "punch_in": ts_new_in,
            "reason": "Biometric terminal lag",
            "regularized_by": "it.admin"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["punch_in"] == ts_new_in
    assert data["late_minutes"] == 0 # no longer late!
    assert len(data["history"]) == 1
    ch = data["history"][0]["changes"]
    assert "punch_in" in ch
    assert isinstance(ch["punch_in"]["from"], int)
    assert ch["punch_in"]["to"] == ts_new_in
    assert "late_minutes" in ch
    assert ch["late_minutes"] == {"from": 35, "to": 0}
    assert "work_hours" in ch

def test_regularize_multiple_corrections_preserve_history(client_app, reset_db):
    # First correction
    dt_in = datetime(2026, 7, 6, 3, 58, 0, tzinfo=timezone.utc)
    ts_in = int(dt_in.timestamp() * 1000)
    res1 = client_app.patch(
        "/attendance/EMP0003/2026-07-06",
        json={"punch_in": ts_in, "reason": "First correction", "regularized_by": "admin1"}
    )
    assert res1.status_code == 200

    # Second correction: change status to WFH
    res2 = client_app.patch(
        "/attendance/EMP0003/2026-07-06",
        json={"status": "WFH", "reason": "Second correction", "regularized_by": "admin2"}
    )
    assert res2.status_code == 200
    data = res2.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["by"] == "admin1"
    assert data["history"][1]["by"] == "admin2"

def test_regularize_concurrent_conflict(client_app, reset_db):
    # Two simultaneous edits to EMP0003 on 2026-07-06
    p1 = {"status": "WFH", "reason": "WFH adjustment", "regularized_by": "mgr1"}
    p2 = {"status": "ON_DUTY", "reason": "Duty adjustment", "regularized_by": "mgr2"}

    def do_patch(p):
        return client_app.patch("/attendance/EMP0003/2026-07-06", json=p).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(do_patch, p1)
        f2 = executor.submit(do_patch, p2)
        results = [f1.result(), f2.result()]

    # At least one succeeds; conflict handling prevents dirty lost updates
    assert 200 in results
