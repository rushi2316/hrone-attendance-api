import concurrent.futures
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def test_punch_in_unknown_employee(client_app, reset_db):
    res = client_app.post("/attendance/punch-in", json={"emp_code": "NONEXISTENT"})
    assert res.status_code == 404

def test_punch_in_invalid_status(client_app, reset_db):
    # ABSENT or LEAVE are not accepted at punch-in
    res = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "status": "ABSENT"})
    assert res.status_code == 422

def test_punch_in_invalid_timestamp(client_app, reset_db):
    # seconds-based timestamp (below 100000000000)
    res = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": 1783312500})
    assert res.status_code == 422

    # boolean
    res_b = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": True})
    assert res_b.status_code == 422

    # float
    res_f = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": 1783312500000.5})
    assert res_f.status_code == 422

def test_punch_in_r2_grace_boundaries(client_app, reset_db):
    # EMP0001 shift is 09:30 - 18:30 IST.
    # Test date 2026-07-20 (a fresh date with no logs)
    # 09:40:00 IST -> 10 mins elapsed -> exactly inside grace period -> late_minutes = 0
    # 2026-07-20 09:40:00 IST is 2026-07-20 04:10:00 UTC
    dt_094000 = datetime(2026, 7, 20, 4, 10, 0, tzinfo=timezone.utc)
    ts_094000 = int(dt_094000.timestamp() * 1000)
    res = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts_094000})
    assert res.status_code == 201
    data = res.json()
    assert data["date"] == "2026-07-20"
    assert data["late_minutes"] == 0
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["history"] == []
    assert "_id" not in data
    assert "id" not in data

    # 2026-07-21: 09:40:01 IST -> 10m01s elapsed -> strictly > 10 mins -> late_minutes = 10
    dt_094001 = datetime(2026, 7, 21, 4, 10, 1, tzinfo=timezone.utc)
    ts_094001 = int(dt_094001.timestamp() * 1000)
    res2 = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts_094001})
    assert res2.status_code == 201
    assert res2.json()["late_minutes"] == 10

    # 2026-07-22: 10:15:59 IST -> 45m59s elapsed -> late_minutes = 45
    dt_101559 = datetime(2026, 7, 22, 4, 45, 59, tzinfo=timezone.utc)
    ts_101559 = int(dt_101559.timestamp() * 1000)
    res3 = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts_101559})
    assert res3.status_code == 201
    assert res3.json()["late_minutes"] == 45

def test_punch_in_overnight_shift(client_app, reset_db):
    # EMP0005 shift is 22:00 - 06:00 IST (overnight)
    # Punch-in at 01:30 IST on 2026-07-16 -> earlier than shift_end 06:00 -> belongs to 2026-07-15!
    # 2026-07-16 01:30:00 IST is 2026-07-15 20:00:00 UTC
    dt_overnight = datetime(2026, 7, 15, 20, 0, 0, tzinfo=timezone.utc)
    ts_overnight = int(dt_overnight.timestamp() * 1000)
    res = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0005", "punched_at": ts_overnight})
    assert res.status_code == 201
    data = res.json()
    assert data["date"] == "2026-07-15" # previous calendar day!

def test_punch_in_duplicate_conflict(client_app, reset_db):
    # EMP0001 already has attendance record for 2026-07-06
    # 2026-07-06 09:30 IST is 2026-07-06 04:00 UTC
    dt = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)
    ts = int(dt.timestamp() * 1000)
    res = client_app.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ts})
    assert res.status_code == 409
    assert "already punched in" in res.json()["detail"].lower()

def test_parallel_duplicate_punch_in(client_app, reset_db):
    # Test date 2026-08-01 for EMP0002
    dt = datetime(2026, 8, 1, 4, 0, 0, tzinfo=timezone.utc)
    ts = int(dt.timestamp() * 1000)
    payload = {"emp_code": "EMP0002", "punched_at": ts}

    def make_punch():
        return client_app.post("/attendance/punch-in", json=payload).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_punch) for _ in range(5)]
        statuses = [f.result() for f in futures]

    assert statuses.count(201) == 1
    assert statuses.count(409) == 4
