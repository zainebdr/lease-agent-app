"""
Tests how the app is assembled: that the frontend is served from the same
origin as the API, and that mounting it at "/" does not swallow the API.

Both halves matter. Serving the page from this app is what removes CORS
from the normal path entirely - the page and the API share an origin, so
the browser has nothing to permit - and that replaced an
`allow_origins=["*"]` that, next to review endpoints with no
authentication, let any site the user had open in another tab read from
and POST to this app on localhost.

Mounting anything at "/" is the kind of change that looks harmless and
silently shadows every other route. Starlette matches registered routes
before mounts, so it doesn't - but "shouldn't" is exactly what a test is
for.
"""
import importlib

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from app import config
from app.main import app


@pytest.fixture()
def raw_client():
    """A client with no DB overrides - these tests are about routing and
    middleware, not data. The app's lifespan is skipped, same as the
    other fixtures, so nothing touches a real app.db."""
    return TestClient(app)


def test_the_frontend_is_served_at_the_root(raw_client):
    resp = raw_client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<title>" in resp.text


def test_mounting_the_frontend_does_not_shadow_the_api(client):
    # If the "/" mount were matched first, each of these would return the
    # HTML page (or a bare StaticFiles 404) instead of its real response.
    # Uses the DB-backed `client` fixture from conftest, because the last
    # assertion has to reach a real route handler to be meaningful.
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/units").status_code == 200

    # A real API route answering for itself: a JSON 404 from the handler,
    # not the static-files fallback.
    resp = client.get("/leases/999999")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "Lease not found."}


def test_an_unknown_path_falls_through_to_the_frontend(raw_client):
    # StaticFiles owns everything the API didn't claim.
    assert raw_client.get("/definitely-not-a-route").status_code == 404


def test_no_cors_middleware_is_installed_by_default():
    """The default is not "a permissive CORS policy" - it is no CORS
    policy, because there is nothing cross-origin to permit."""
    assert config.CORS_ORIGINS == []
    assert not any(m.cls is CORSMiddleware for m in app.user_middleware)


def test_cors_origins_is_an_explicit_allowlist_never_a_wildcard(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5500, https://app.example.com")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.CORS_ORIGINS == ["http://localhost:5500", "https://app.example.com"]
        assert "*" not in reloaded.CORS_ORIGINS
    finally:
        monkeypatch.delenv("CORS_ORIGINS")
        importlib.reload(config)


def test_the_wildcard_is_gone_from_the_source():
    """A regression guard with teeth: the failure mode is somebody
    re-adding allow_origins=["*"] while debugging and never taking it
    out. Reading the file is crude, and it is the only check that
    survives that."""
    source = (config.BASE_DIR / "app" / "main.py").read_text()
    # Comments are stripped first - the file explains at length why the
    # wildcard was removed, and that explanation must not be what trips
    # this test.
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert 'allow_origins=["*"]' not in code
    # The only thing allow_origins may ever be set from is the config
    # allowlist. (allow_methods / allow_headers staying "*" is fine -
    # they are only reachable once an explicit origin has been named.)
    assert "allow_origins=CORS_ORIGINS" in code
