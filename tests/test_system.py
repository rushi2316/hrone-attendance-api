from unittest.mock import patch
from pymongo.errors import ConnectionFailure

def test_health_ok(client_app):
    response = client_app.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_health_503_when_mongo_fails(client_app):
    import app.main
    with patch.object(app.main.db, "command", side_effect=ConnectionFailure("Connection refused")):
        response = client_app.get("/health")
        assert response.status_code == 503
        assert "Database unavailable" in response.json()["detail"]
