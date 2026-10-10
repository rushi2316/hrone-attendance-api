import os
import pathlib
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Set test environment variable before importing app
os.environ["MONGO_DB"] = "attendance_test_db"

import pytest
from bson import json_util
from fastapi.testclient import TestClient

from app.main import app, client, db

@pytest.fixture(scope="session", autouse=True)
def setup_indexes():
    # Run startup index creation
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.employees.create_index([("joined_on", 1)])

    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)])
    yield
    # Cleanup after test session
    client.drop_database("attendance_test_db")

@pytest.fixture
def reset_db():
    """Resets test database with sample seed data."""
    here = pathlib.Path(__file__).parent.parent / "sample_data"
    opts = json_util.JSONOptions(tz_aware=True)
    db.employees.delete_many({})
    db.attendance_logs.delete_many({})

    emp_docs = json_util.loads((here / "employees.json").read_text(), json_options=opts)
    att_docs = json_util.loads((here / "attendance_logs.json").read_text(), json_options=opts)
    if emp_docs:
        db.employees.insert_many(emp_docs)
    if att_docs:
        db.attendance_logs.insert_many(att_docs)
    return db

@pytest.fixture
def client_app():
    return TestClient(app)
