from fastapi.testclient import TestClient


def test_unknown_route_returns_standard_error_shape(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404

    body = response.json()
    assert body["error"]["code"] == "http_error"
    assert "message" in body["error"]


def test_unknown_route_does_not_leak_internal_detail(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    body = response.json()
    assert "traceback" not in response.text.lower()
    assert set(body.keys()) == {"error"}
