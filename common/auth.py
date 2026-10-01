"""Shared authentication.

Imported by BOTH the Locust suite and the pytest suite so there is one
implementation of PMT login to maintain, not two.

ADAPT THIS FILE to your real auth flow. Everything else depends on
`session_for(role)` returning a requests.Session with credentials attached.
"""
import threading
import requests

from common.config import env

_LOCK = threading.Lock()
_TOKEN_CACHE = {}

ROLE_CREDS = {
    "user":     ("PMT_USER", "PMT_PASSWORD"),
    "admin":    ("PMT_ADMIN_USER", "PMT_ADMIN_PASSWORD"),
    "readonly": ("PMT_READONLY_USER", "PMT_READONLY_PASSWORD"),
}


def base_url():
    return env("PMT_BASE_URL", required=True).rstrip("/")


def verify_tls():
    return env("PMT_VERIFY_TLS", "true").lower() != "false"


def _login(username, password):
    """Exchange credentials for a bearer token.

    ADAPT: change the path and the response field to match PMT.
    """
    resp = requests.post(
        base_url() + "/api/auth/login",
        json={"username": username, "password": password},
        timeout=30,
        verify=verify_tls(),
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def token_for(role="user", refresh=False):
    if role not in ROLE_CREDS:
        raise ValueError("unknown role: %s" % role)
    with _LOCK:
        if refresh or role not in _TOKEN_CACHE:
            user_var, pass_var = ROLE_CREDS[role]
            _TOKEN_CACHE[role] = _login(
                env(user_var, required=True), env(pass_var, required=True)
            )
        return _TOKEN_CACHE[role]


def session_for(role="user"):
    s = requests.Session()
    s.verify = verify_tls()
    s.headers.update(
        {
            "Authorization": "Bearer " + token_for(role),
            "Accept": "application/json",
        }
    )
    return s


def invalidate(role="user"):
    """Force the next call to re-login. Used by NEG-16 (token expiry)."""
    with _LOCK:
        _TOKEN_CACHE.pop(role, None)
