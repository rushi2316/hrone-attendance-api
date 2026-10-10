def find_stages(obj, found=None):
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "stage" in obj:
            found.append(obj["stage"])
        for v in obj.values():
            find_stages(v, found)
    elif isinstance(obj, list):
        for item in obj:
            find_stages(item, found)
    return found

def test_explain_attendance_list(client_app, reset_db):
    res = client_app.get("/admin/explain/attendance_list?date_from=2026-07-01&date_to=2026-07-31")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "attendance_list"
    assert data["collection"] == "attendance_logs"
    stages = find_stages(data["explain"])
    assert "IXSCAN" in stages
    assert "COLLSCAN" not in stages

def test_explain_employee_monthly(client_app, reset_db):
    res = client_app.get("/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "employee_monthly"
    assert data["collection"] == "attendance_logs"
    stages = find_stages(data["explain"])
    assert "IXSCAN" in stages
    assert "COLLSCAN" not in stages

def test_explain_employee_monthly_missing_params_422(client_app, reset_db):
    res = client_app.get("/admin/explain/employee_monthly")
    assert res.status_code == 422

def test_explain_department_summary(client_app, reset_db):
    res = client_app.get("/admin/explain/department_summary?month=2026-07&department=Sales")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "department_summary"
    assert data["collection"] == "employees"
    stages = find_stages(data["explain"])
    assert "IXSCAN" in stages
    assert "COLLSCAN" not in stages

def test_explain_late_leaderboard(client_app, reset_db):
    res = client_app.get("/admin/explain/late_leaderboard?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "late_leaderboard"
    assert data["collection"] == "attendance_logs"
    stages = find_stages(data["explain"])
    assert "IXSCAN" in stages
    assert "COLLSCAN" not in stages

def test_explain_department_trend(client_app, reset_db):
    res = client_app.get("/admin/explain/department_trend?department=Sales&from=2026-07-01&to=2026-07-10")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "department_trend"
    assert data["collection"] == "employees"
    stages = find_stages(data["explain"])
    assert "IXSCAN" in stages
    assert "COLLSCAN" not in stages

def test_explain_invalid_endpoint_name_422(client_app, reset_db):
    res = client_app.get("/admin/explain/unknown_endpoint")
    assert res.status_code == 422
