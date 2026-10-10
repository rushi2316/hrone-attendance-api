import concurrent.futures
from datetime import datetime, timezone, timedelta

def test_punch_out_unknown_employee(client_app, reset_db):
    res = client_app.post("/attendance/punch-out", json={"emp_code": "NONEXISTENT"})
    assert res.status_code == 404

def test_punch_out_no_punch_in(client_app, reset_db):
    # EMP0002 has no punch-in on 2026-07-01
    dt = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    ts = int(dt.timestamp() * 1000)
    res = client_app.post("/attendance/punch-out", json={"emp_code": "EMP0002", "punched_at": ts})
    assert res.status_code == 404

def test_punch_out_success_and_derived_fields(client_app, reset_db):
    # EMP0006 has an open punch-in on 2026-07-07:
    # punch_in is 2026-07-07T04:05:00Z (09:35 IST). Shift is 09:30 - 18:30 IST.
    # Let's punch out at 19:15:00 IST (13:45:00 UTC) on 2026-07-07
    # Duration: 04:05:00 UTC to 13:45:00 UTC = 9 hours 40 minutes = 9.6666... hours -> 9.67 hours.
    # Overtime: 19:15 - 18:30 = 45 minutes (>= 30) -> 45 minutes.
    # half_day: 9.67 >= 4.50 -> False.
    dt_out = datetime(2026, 7, 7, 13, 45, 0, tzinfo=timezone.utc)
    ts_out = int(dt_out.timestamp() * 1000)

    res = client_app.post("/attendance/punch-out", json={"emp_code": "EMP0006", "punched_at": ts_out})
    assert res.status_code == 200
    data = res.json()
    assert data["emp_code"] == "EMP0006"
    assert data["date"] == "2026-07-07"
    assert data["punch_out"] == ts_out
    assert data["work_hours"] == 9.67
    assert data["overtime_minutes"] == 45
    assert data["half_day"] is False
    assert data["history"] == []
    assert "_id" not in data
    assert "id" not in data

def test_punch_out_already_punched_out_conflict(client_app, reset_db):
    # EMP0001 on 2026-07-06 is already punched out
    dt = datetime(2026, 7, 6, 14, 0, 0, tzinfo=timezone.utc)
    ts = int(dt.timestamp() * 1000)
    res = client_app.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ts})
    assert res.status_code == 409
    assert "already punched out" in res.json()["detail"].lower()

def test_punch_out_before_or_at_punch_in(client_app, reset_db):
    # EMP0006 punch_in is 2026-07-07T04:05:00Z
    # punch_out at 2026-07-07T04:05:00Z -> equal -> 422
    dt_equal = datetime(2026, 7, 7, 4, 5, 0, tzinfo=timezone.utc)
    res = client_app.post("/attendance/punch-out", json={"emp_code": "EMP0006", "punched_at": int(dt_equal.timestamp() * 1000)})
    assert res.status_code == 422

    # punch_out earlier than punch_in (no punch-in at or before punched_at) -> 404
    dt_before = datetime(2026, 7, 7, 4, 0, 0, tzinfo=timezone.utc)
    res_b = client_app.post("/attendance/punch-out", json={"emp_code": "EMP0006", "punched_at": int(dt_before.timestamp() * 1000)})
    assert res_b.status_code == 404

def test_punch_out_more_than_24_hours(client_app, reset_db):
    # EMP0006 punch_in is 2026-07-07T04:05:00Z
    # punch_out 25 hours later -> 422
    dt_late = datetime(2026, 7, 8, 5, 10, 0, tzinfo=timezone.utc)
    res = client_app.post("/attendance/punch-out", json={"emp_code": "EMP0006", "punched_at": int(dt_late.timestamp() * 1000)})
    assert res.status_code == 422

def test_punch_out_half_day_flag(client_app, reset_db):
    # Create an employee and punch in
    emp_code = "EMP5555"
    client_app.post("/employees", json={
        "emp_code": emp_code,
        "name": "Half Day Test",
        "email": "half@example.com",
        "department": "Engineering",
        "joined_on": "2026-01-01"
    })
    dt_in = datetime(2026, 7, 20, 4, 0, 0, tzinfo=timezone.utc) # 09:30 IST
    client_app.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": int(dt_in.timestamp() * 1000)})

    # Punch out after 4.0 hours (less than 4.50)
    dt_out = datetime(2026, 7, 20, 8, 0, 0, tzinfo=timezone.utc)
    res = client_app.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": int(dt_out.timestamp() * 1000)})
    assert res.status_code == 200
    data = res.json()
    assert data["work_hours"] == 4.0
    assert data["half_day"] is True

def test_punch_out_overtime_under_threshold(client_app, reset_db):
    # Shift ends at 18:30 IST (13:00 UTC).
    # Punch out at 18:45 IST (13:15 UTC) -> 15 min elapsed (< 30) -> overtime_minutes = 0
    emp_code = "EMP5556"
    client_app.post("/employees", json={
        "emp_code": emp_code,
        "name": "OT Test",
        "email": "ot@example.com",
        "department": "Engineering",
        "joined_on": "2026-01-01"
    })
    dt_in = datetime(2026, 7, 20, 4, 0, 0, tzinfo=timezone.utc)
    client_app.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": int(dt_in.timestamp() * 1000)})

    dt_out = datetime(2026, 7, 20, 13, 15, 0, tzinfo=timezone.utc)
    res = client_app.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": int(dt_out.timestamp() * 1000)})
    assert res.status_code == 200
    assert res.json()["overtime_minutes"] == 0

def test_parallel_duplicate_punch_out(client_app, reset_db):
    # EMP0006 is open
    dt_out = datetime(2026, 7, 7, 13, 0, 0, tzinfo=timezone.utc)
    ts_out = int(dt_out.timestamp() * 1000)
    payload = {"emp_code": "EMP0006", "punched_at": ts_out}

    def make_punch_out():
        return client_app.post("/attendance/punch-out", json=payload).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_punch_out) for _ in range(5)]
        statuses = [f.result() for f in futures]

    assert statuses.count(200) == 1
    assert statuses.count(409) == 4
