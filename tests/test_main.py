from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to Sapio AI Backend",
        "version": "0.1.0",
        "docs": "/docs",
    }


def test_health_endpoint():
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "Sapio AI Backend",
    }


def test_unknown_endpoint():
    response = client.get("/api/v1/not-found")

    assert response.status_code == 404


def test_openapi_schema():
    response = client.get("/openapi.json")

    assert response.status_code == 200

    schema = response.json()

    assert schema["info"]["title"] == "Sapio AI Backend"
    assert "/api/v1/health" in schema["paths"]
