import os
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

def test_get_token():
    # Set dummy env vars for test
    os.environ["LIVEKIT_API_KEY"] = "test_key"
    os.environ["LIVEKIT_API_SECRET"] = "test_secret"
    
    response = client.get("/token?room=test_room&name=candidate")
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert isinstance(data["token"], str)

def test_get_token_missing_params():
    response = client.get("/token")
    # FastAPI's query params will return 422 Unprocessable Entity when missing required parameters
    assert response.status_code == 422
