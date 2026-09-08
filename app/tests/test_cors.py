"""CORS is what lets the browser-based frontend call the API cross-origin."""

ALLOWED = "http://localhost:4200"
DISALLOWED = "http://evil.example"


def test_preflight_from_an_allowed_origin_is_permitted(client) -> None:
    resp = client.options(
        "/auth/login",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED


def test_simple_request_from_an_allowed_origin_is_echoed(client) -> None:
    resp = client.get("/", headers={"Origin": "http://127.0.0.1:4200"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://127.0.0.1:4200"


def test_request_from_a_disallowed_origin_gets_no_cors_headers(client) -> None:
    resp = client.get("/", headers={"Origin": DISALLOWED})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers
