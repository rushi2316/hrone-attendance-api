from datetime import datetime, timezone, timedelta
from decimal import Decimal
from app.main import round_half_up, compute_work_hours, compute_late_minutes, compute_overtime, get_attendance_date

def test_round_half_up_exact_half_cases():
    # Built-in Python round(2.505, 2) is 2.5 (banker's round to even).
    # R8 requires half-up rounding: 2.505 -> 2.51, 2.515 -> 2.52
    assert round_half_up(2.505, 2) == 2.51
    assert round_half_up(2.515, 2) == 2.52
    assert round_half_up(2.525, 2) == 2.53
    assert round_half_up(2.535, 2) == 2.54
    assert round_half_up(2.545, 2) == 2.55
    assert round_half_up(0.12505, 4) == 0.1251
    assert round_half_up(None, 2) is None

def test_overnight_shift_date_boundary():
    # 22:00 to 06:00 shift.
    # Punch in at 05:59:59 IST on 2026-07-15 -> belongs to 2026-07-14
    # 05:59:59 IST is 00:29:59 UTC on 2026-07-15
    dt1 = datetime(2026, 7, 15, 0, 29, 59, tzinfo=timezone.utc)
    assert get_attendance_date(dt1, "22:00", "06:00") == "2026-07-14"

    # Punch in at 06:00:00 IST on 2026-07-15 -> not < 06:00 -> belongs to 2026-07-15
    dt2 = datetime(2026, 7, 15, 0, 30, 0, tzinfo=timezone.utc)
    assert get_attendance_date(dt2, "22:00", "06:00") == "2026-07-15"

def test_overnight_shift_overtime_calculation():
    # Shift 22:00 - 06:00, date 2026-07-14. Shift ends 2026-07-15 06:00 IST (00:30 UTC).
    # Punch-out at 06:30 IST (01:00 UTC) -> 30 mins overtime (>= 30) -> 30
    dt_out30 = datetime(2026, 7, 15, 1, 0, 0, tzinfo=timezone.utc)
    assert compute_overtime(dt_out30, "22:00", "06:00", "2026-07-14") == 30

    # Punch-out at 06:29 IST (00:59 UTC) -> 29 mins overtime (< 30) -> 0
    dt_out29 = datetime(2026, 7, 15, 0, 59, 0, tzinfo=timezone.utc)
    assert compute_overtime(dt_out29, "22:00", "06:00", "2026-07-14") == 0

def test_mid_month_joiner_after_month_end(client_app, reset_db):
    # Employee joined in August 2026
    emp_code = "EMP7001"
    client_app.post("/employees", json={
        "emp_code": emp_code,
        "name": "Future Joiner",
        "email": "future@example.com",
        "department": "Engineering",
        "joined_on": "2026-08-15"
    })

    # In July 2026, employee had not joined yet
    res = client_app.get(f"/analytics/employees/{emp_code}/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 0
    assert data["attendance_pct"] is None

def test_department_summary_omits_zero_headcount_dept(client_app, reset_db):
    # Create an employee in a new department 'HR' who joins in 2026-09-01
    client_app.post("/employees", json={
        "emp_code": "EMP7002",
        "name": "HR Person",
        "email": "hr@example.com",
        "department": "HumanResources",
        "joined_on": "2026-09-01"
    })

    # For 2026-07, HumanResources has 0 headcount and must be omitted per R9/contract
    res = client_app.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200
    depts = [item["department"] for item in res.json()["items"]]
    assert "HumanResources" not in depts
