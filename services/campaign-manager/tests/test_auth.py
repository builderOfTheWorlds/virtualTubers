"""Optional HTTP Basic Auth on campaign-manager (plan Phase 0).

Mirrors services/control-panel, copied verbatim rather than reinvented —
plan decision 1's stated reason: the two services converge on one auth
model rather than each inventing a different one. Same functions, same
`secrets.compare_digest` timing-safe comparison, same /healthz bypass (so
the container healthcheck never needs credentials), same default-off
(unless BOTH the user AND the password env vars are set).

Two environment-specific shapes this test file has to respect, both
discovered the hard way:

  1. main.py reads `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` as module-level
     globals set once at import time, so tests monkeypatch the attributes
     (restored automatically per test) rather than re-executing the module.
  2. starlette's TestClient runs the async app inside a portal with its own
     event loop, while `main.http_client` (the shared async httpx client
     used to proxy to the generator API) participates in that loop.
     Creating a throwaway TestClient *inside* each test crashes
     intermittently with "Event loop is closed" once a later test's loop
     closes while an earlier one's client is still settling. One
     module-scoped TestClient, entered once, avoids the whole class of
     flake.
"""
import base64

import pytest
from fastapi.testclient import TestClient

import main as api


def _basic(user, pw):
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(scope="module")
def client():
    c = TestClient(api.app)
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(api, "BASIC_AUTH_USER", "op")
    monkeypatch.setattr(api, "BASIC_AUTH_PASS", "secret")


@pytest.fixture
def auth_off(monkeypatch):
    monkeypatch.setattr(api, "BASIC_AUTH_USER", "")
    monkeypatch.setattr(api, "BASIC_AUTH_PASS", "")


def test_no_credentials_is_401(client, auth_on):
    resp = client.get("/")
    assert resp.status_code == 401
    assert 'Basic realm="campaign-manager"' in resp.headers.get("WWW-Authenticate", "")


def test_correct_credentials_pass(client, auth_on):
    assert client.get("/", headers=_basic("op", "secret")).status_code != 401


def test_wrong_password_is_401(client, auth_on):
    assert client.get("/", headers=_basic("op", "wrong")).status_code == 401


def test_wrong_username_is_401(client, auth_on):
    assert client.get("/", headers=_basic("intruder", "secret")).status_code == 401


def test_malformed_authorization_header_is_401_not_a_crash(client, auth_on):
    """A header that fails base64 decode must be a clean 401 (the
    middleware catches the decode error and rejects), not a 500 / unhandled
    exception."""
    resp = client.get("/", headers={"Authorization": "Basic !not-base64!"})
    assert resp.status_code == 401


def test_healthz_never_requires_auth(client, auth_on):
    assert client.get("/healthz").status_code == 200


def test_auth_is_a_noop_when_unset(client, auth_off):
    # `not _auth_enabled()` must short-circuit before the Authorization
    # header is even read: an unauthenticated GET / must not be a 401.
    assert client.get("/").status_code != 401
