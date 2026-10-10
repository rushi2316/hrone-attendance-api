def test_list_attendance_pagination_and_sorting(client_app, reset_db):
    res = client_app.get("/attendance?page=1&page_size=5")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 9
    assert len(data["items"]) == 5
    assert data["page"] == 1
    assert data["page_size"] == 5

    # Check sort: date descending, then emp_code ascending
    items = data["items"]
    for i in range(len(items) - 1):
        d1 = items[i]["date"]
        d2 = items[i + 1]["date"]
        e1 = items[i]["emp_code"]
        e2 = items[i + 1]["emp_code"]
        assert (d1 > d2) or (d1 == d2 and e1 <= e2)

    # Check contract fields
    for item in items:
        assert "_id" not in item
        assert "id" not in item
        if item["punch_in"] is not None:
            assert isinstance(item["punch_in"], int)
        if item["punch_out"] is not None:
            assert isinstance(item["punch_out"], int)

def test_list_attendance_date_range_invalid(client_app, reset_db):
    # date_from > date_to -> 422
    res = client_app.get("/attendance?date_from=2026-07-10&date_to=2026-07-05")
    assert res.status_code == 422

def test_list_attendance_filters(client_app, reset_db):
    # Filter by emp_code
    res_emp = client_app.get("/attendance?emp_code=EMP0001")
    assert res_emp.status_code == 200
    data_emp = res_emp.json()
    assert data_emp["total"] == 2
    assert len(data_emp["items"]) == 2
    for it in data_emp["items"]:
        assert it["emp_code"] == "EMP0001"

    # Filter by status
    res_wfh = client_app.get("/attendance?status=WFH")
    assert res_wfh.status_code == 200
    data_wfh = res_wfh.json()
    assert data_wfh["total"] == 1
    assert data_wfh["items"][0]["status"] == "WFH"

    # Filter by date range (3 on 2026-07-07, 2 on 2026-07-08 = 5 total)
    res_range = client_app.get("/attendance?date_from=2026-07-07&date_to=2026-07-08")
    assert res_range.status_code == 200
    data_range = res_range.json()
    assert data_range["total"] == 5
