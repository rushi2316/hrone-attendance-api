from datetime import datetime, timezone

def test_employee_monthly_summary(client_app, reset_db):
    # EMP0001 in 2026-07 (joined 2026-01-05).
    # July 2026 has 23 working days (Mon-Fri).
    # Logs for EMP0001: 2026-07-06 (Mon, PRESENT, 0 late), 2026-07-07 (Tue, PRESENT, 0 late).
    # present_days = 2.0. working_days = 23.
    # attendance_pct = 2 / 23 * 100 = 8.695652... -> 8.6957 (4 decimals half-up)
    res = client_app.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert data["month"] == "2026-07"
    assert data["working_days"] == 23
    assert data["present_days"] == 2.0
    assert data["leave_days"] == 0
    assert data["late_count"] == 0
    assert data["attendance_pct"] == 8.6957

def test_employee_monthly_mid_month_joiner(client_app, reset_db):
    # EMP0002 joined on 2026-07-13 (Monday).
    # July 2026 working days from 2026-07-13 to 2026-07-31:
    # 2026-07-13 is Monday. Weeks: 13-17 (5), 20-24 (5), 27-31 (5) = 15 working days!
    res = client_app.get("/analytics/employees/EMP0002/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 15
    assert data["present_days"] == 1.0 # 2026-07-14 log
    # attendance_pct: 1 / 15 * 100 = 6.66666... -> 6.6667
    assert data["attendance_pct"] == 6.6667

def test_employee_monthly_unknown_employee(client_app, reset_db):
    res = client_app.get("/analytics/employees/NONEXISTENT/monthly?month=2026-07")
    assert res.status_code == 404

def test_department_summary(client_app, reset_db):
    res = client_app.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["month"] == "2026-07"
    depts = {item["department"]: item for item in data["items"]}

    # Engineering: headcount 2, present_days 3.0, avg_work_hours 9.04
    eng = depts["Engineering"]
    assert eng["headcount"] == 2
    assert eng["present_days"] == 3.0
    assert eng["avg_work_hours"] == 9.04
    assert eng["late_count"] == 0
    assert eng["total_late_minutes"] == 0

    # Sales: headcount 2, present_days 1.5 (Rohit half day WFH + Neha 1.0), avg_work_hours 6.29, late 35
    sales = depts["Sales"]
    assert sales["headcount"] == 2
    assert sales["present_days"] == 1.5
    assert sales["avg_work_hours"] == 6.29
    assert sales["late_count"] == 1
    assert sales["total_late_minutes"] == 35
    assert sales["leave_count"] == 1

    # Support: headcount 2, present_days 2.0, avg_work_hours 8.75
    supp = depts["Support"]
    assert supp["headcount"] == 2
    assert supp["present_days"] == 2.0
    assert supp["avg_work_hours"] == 8.75

def test_department_summary_with_filter(client_app, reset_db):
    res = client_app.get("/analytics/departments/summary?month=2026-07&department=Sales")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["department"] == "Sales"

def test_late_leaderboard_competition_ranking(client_app, reset_db):
    # In sample data, only EMP0003 has late_minutes > 0 (35 mins)
    res = client_app.get("/analytics/leaderboard/late?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["rank"] == 1
    assert item["emp_code"] == "EMP0003"
    assert item["name"] == "Neha Iyer"
    assert item["department"] == "Sales"
    assert item["total_late_minutes"] == 35
    assert item["late_count"] == 1

def test_late_leaderboard_ties_at_cutoff(client_app, reset_db):
    # Insert multiple employees with tied late minutes
    db = reset_db
    # Seed 3 employees tied at 40 mins
    db.employees.insert_many([
        {"emp_code": f"EMP001{i}", "name": f"Tied {i}", "email": f"t{i}@ex.com", "department": "Engineering", "shift_start": "09:30", "shift_end": "18:30", "joined_on": "2026-01-01"}
        for i in range(1, 4)
    ])
    dt = datetime(2026, 7, 10, 4, 30, 0, tzinfo=timezone.utc)
    db.attendance_logs.insert_many([
        {"emp_code": f"EMP001{i}", "date": "2026-07-10", "status": "PRESENT", "punch_in": dt, "punch_out": None, "late_minutes": 40, "work_hours": None, "half_day": False, "history": []}
        for i in range(1, 4)
    ])

    # With limit=2, the 3 tied employees have rank 1, and EMP0003 has rank 4.
    # Because 1 <= 2, all 3 tied employees are returned!
    res = client_app.get("/analytics/leaderboard/late?month=2026-07&limit=2")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 3
    for it in items:
        assert it["rank"] == 1
        assert it["total_late_minutes"] == 40

def test_department_trend(client_app, reset_db):
    res = client_app.get("/analytics/departments/Sales/trend?from=2026-07-01&to=2026-07-10")
    assert res.status_code == 200
    data = res.json()
    assert data["department"] == "Sales"
    items = data["items"]
    # Exactly 10 days
    assert len(items) == 10
    dates = [it["date"] for it in items]
    assert dates[0] == "2026-07-01"
    assert dates[-1] == "2026-07-10"

    # Weekends have attendance_rate = None
    assert items[3]["date"] == "2026-07-04" # Saturday
    assert items[3]["is_working_day"] is False
    assert items[3]["attendance_rate"] is None

    # Check moving average on 2026-07-06 (Monday):
    # Rates on working days: 07-01 (0.0), 07-02 (0.0), 07-03 (0.0), 07-04 (None), 07-05 (None), 07-06 (0.5)
    # Average of [0.0, 0.0, 0.0, 0.5] = 0.1250
    assert items[5]["date"] == "2026-07-06"
    assert items[5]["attendance_rate"] == 0.5
    assert items[5]["moving_avg_7d"] == 0.125

def test_department_trend_range_limit_and_order(client_app, reset_db):
    # to < from -> 422
    res_inv = client_app.get("/analytics/departments/Sales/trend?from=2026-07-10&to=2026-07-01")
    assert res_inv.status_code == 422

    # range > 92 days -> 422
    res_long = client_app.get("/analytics/departments/Sales/trend?from=2026-01-01&to=2026-05-01")
    assert res_long.status_code == 422

    # unknown department -> 404
    res_404 = client_app.get("/analytics/departments/NONEXISTENT/trend?from=2026-07-01&to=2026-07-10")
    assert res_404.status_code == 404
