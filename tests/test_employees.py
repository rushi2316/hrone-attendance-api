import concurrent.futures
import pytest

def test_create_employee_success(client_app, reset_db):
    payload = {
        "emp_code": "EMP9999",
        "name": "Test Engineer",
        "email": "test.eng@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-06-01",
    }
    response = client_app.post("/employees", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["emp_code"] == "EMP9999"
    assert "_id" not in data
    assert "id" not in data
    assert isinstance(data["created_at"], int)
    assert data["created_at"] >= 100000000000

def test_create_duplicate_employee_conflict(client_app, reset_db):
    payload = {
        "emp_code": "EMP0001", # Already seeded in EMP0001
        "name": "Duplicate Asha",
        "email": "asha2@example.com",
        "department": "Engineering",
        "joined_on": "2026-01-05",
    }
    response = client_app.post("/employees", json=payload)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]

def test_create_employee_invalid_shift_same_start_end(client_app, reset_db):
    payload = {
        "emp_code": "EMP8888",
        "name": "Invalid Shift",
        "email": "invalid@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "09:30",
        "joined_on": "2026-06-01",
    }
    response = client_app.post("/employees", json=payload)
    assert response.status_code == 422

def test_create_employee_invalid_emp_code_pattern(client_app, reset_db):
    payload = {
        "emp_code": "INVALID_CODE",
        "name": "Invalid Emp Code",
        "email": "invalid@example.com",
        "department": "Engineering",
        "joined_on": "2026-06-01",
    }
    response = client_app.post("/employees", json=payload)
    assert response.status_code == 422

def test_list_employees_pagination_and_filter(client_app, reset_db):
    # Total seeded employees is 6 (Engineering: 2, Sales: 2, Support: 2)
    res_all = client_app.get("/employees?page=1&page_size=4")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["total"] == 6
    assert data_all["page"] == 1
    assert data_all["page_size"] == 4
    assert len(data_all["items"]) == 4
    # Check sorted by emp_code ascending
    emp_codes = [i["emp_code"] for i in data_all["items"]]
    assert emp_codes == sorted(emp_codes)

    # Page 2
    res_p2 = client_app.get("/employees?page=2&page_size=4")
    assert res_p2.status_code == 200
    data_p2 = res_p2.json()
    assert data_p2["total"] == 6
    assert data_p2["page"] == 2
    assert len(data_p2["items"]) == 2

    # Department filter: exact case-sensitive
    res_sales = client_app.get("/employees?department=Sales")
    assert res_sales.status_code == 200
    data_sales = res_sales.json()
    assert data_sales["total"] == 2
    assert len(data_sales["items"]) == 2
    for item in data_sales["items"]:
        assert item["department"] == "Sales"
        assert "_id" not in item
        assert "id" not in item
        assert isinstance(item["created_at"], int)

def test_parallel_duplicate_employee_creation(client_app, reset_db):
    payload = {
        "emp_code": "EMP7777",
        "name": "Parallel Emp",
        "email": "parallel@example.com",
        "department": "Engineering",
        "joined_on": "2026-05-01",
    }
    def make_request():
        return client_app.post("/employees", json=payload).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_request) for _ in range(5)]
        statuses = [f.result() for f in futures]

    assert statuses.count(201) == 1
    assert statuses.count(409) == 4
