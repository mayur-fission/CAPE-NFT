"""Retry/backoff shared by session_batch.py and session_concurrent.py, so
both use the same retry budget and backoff.
"""
import threading
import time

_LAUNCH_ATTEMPTS = 3

# Workbench rejects two sign-ins for the same account that land too close
# together (see _stagger_login()). Seconds enforced between login starts.
_LOGIN_MIN_GAP_S = 3.0

_login_lock = threading.Lock()
_last_login_start = [0.0]


def _stagger_login():
    """Block until _LOGIN_MIN_GAP_S has passed since the last login started
    anywhere in the process, then claim the slot.

    Workbench rejects simultaneous logins to the same account (error=2), and
    workers that fail together would otherwise retry in lockstep. Only login
    is serialized - it isn't timed, so session launches still overlap.
    """
    with _login_lock:
        gap = _LOGIN_MIN_GAP_S - (time.time() - _last_login_start[0])
        if gap > 0:
            time.sleep(gap)
        _last_login_start[0] = time.time()


def _retry_backoff_s(attempt):
    """Seconds to wait before retrying after a failed attempt (0-based)."""
    return 2 + attempt * 2
