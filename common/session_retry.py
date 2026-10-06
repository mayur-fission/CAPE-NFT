"""Retry and login staggering shared by every multi-session flow, so they all
use the same retry budget and backoff.
"""
import threading
import time

LAUNCH_ATTEMPTS = 3

# Workbench rejects two sign-ins for the same account that land too close
# together (error=2). Seconds enforced between login starts.
_LOGIN_MIN_GAP_S = 3.0

_login_lock = threading.Lock()
_last_login_start = [0.0]


def stagger_login():
    """Block until _LOGIN_MIN_GAP_S has passed since the last login started
    anywhere in the process, then claim the slot.

    Workers that fail together would otherwise also retry in lockstep. Only
    login is serialized - it isn't timed, so session launches still overlap.
    """
    with _login_lock:
        gap = _LOGIN_MIN_GAP_S - (time.time() - _last_login_start[0])
        if gap > 0:
            time.sleep(gap)
        _last_login_start[0] = time.time()


def retry_backoff_s(attempt):
    """Seconds to wait before retrying after failed attempt `attempt` (0-based)."""
    return 2 + attempt * 2


def retry(fn, what, attempts=LAUNCH_ATTEMPTS):
    """Call fn(attempt_number) (1-based) until it returns, up to `attempts`
    times with backoff, printing each failure as "<what> attempt i/n failed".
    Returns (value, attempts_used); raises the last error if all fail.
    """
    for attempt in range(attempts):
        try:
            value = fn(attempt + 1)
        except Exception as exc:
            print("[rstudio-local] %s attempt %d/%d failed: %s" % (what, attempt + 1, attempts, exc))
            if attempt + 1 == attempts:
                raise
            time.sleep(retry_backoff_s(attempt))
        else:
            if attempt:
                print("[rstudio-local] %s succeeded on attempt %d/%d" % (what, attempt + 1, attempts))
            return value, attempt + 1


def result_with_retries(index, session_name, fn, what=None, attempts=LAUNCH_ATTEMPTS):
    """retry() for one session of a multi-session run, as a result dict.

    fn(attempt_number) returns the run's own fields. On success returns
    {"index", "ok": True, "session_name", "attempts", **fields}; if every
    attempt fails, {"index", "ok": False, "error", "session_name",
    "attempts"}. Never raises.
    """
    try:
        fields, used = retry(fn, what or "session %s" % session_name, attempts)
    except Exception as exc:
        return {"index": index, "ok": False, "error": str(exc), "session_name": session_name, "attempts": attempts}
    return dict({"index": index, "ok": True, "session_name": session_name, "attempts": used}, **fields)
