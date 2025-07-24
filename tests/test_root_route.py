from fastapi.testclient import TestClient
from main import app

def test_root_endpoint():
    client = TestClient(app)
    resp = client.get('/')
    assert resp.status_code == 200
    assert resp.json() == {"message": "See /docs for API documentation"}
