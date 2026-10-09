"""Retry and login staggering shared by every multi-session flow, so they all
use the same retry budget and backoff.
"""
import os
import threading
import time
from contextlib import contextmanager

from common.config import EVIDENCE_DIR, env

LAUNCH_ATTEMPTS = 3

# Workbench rejects two sign-ins for the same account that land too close
# together (error=2). Seconds enforced between login starts.
_LOGIN_MIN_GAP_S = 3.0

# Seconds between login starts across every test process on this machine
# that shares the workspace (e.g. tests run in parallel by Jenkins with
# RUN_IN_PARALLEL), see wait_for_login_slot().
LOGIN_MIN_INTERVAL_S = float(env("LOGIN_MIN_INTERVAL_S", str(_LOGIN_MIN_GAP_S)))
_LOGIN_SLOT_LOCK = os.path.join(EVIDENCE_DIR, ".login_slot.lock")
_LOGIN_SLOT_LAST = os.path.join(EVIDENCE_DIR, ".login_slot_last")
# A lock file older than this was left by a killed process.
_LOGIN_SLOT_LOCK_STALE_S = 600

_login_lock = threading.Lock()
_last_login_start = [0.0]


@contextmanager
def _file_lock(path, stale_s, poll_s=0.5):
    """Hold the lock file `path` (created exclusively) for the block, so only
    one process at a time is inside. A lock older than stale_s is removed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > stale_s:
                    os.remove(path)
                    continue
            except OSError:
                continue  # released meanwhile
            time.sleep(poll_s)
    try:
        yield
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def wait_for_login_slot(gap_s=None):
    """Block until `gap_s` (default LOGIN_MIN_INTERVAL_S) has passed since
    the last login started in any test process or thread on this machine,
    then claim the slot. Workbench fails sign-ins that land at the same
    time, so login_to_posit_workbench() calls this before every login.
    """
    gap_s = LOGIN_MIN_INTERVAL_S if gap_s is None else gap_s
    if gap_s <= 0:
        return
    with _login_lock, _file_lock(_LOGIN_SLOT_LOCK, _LOGIN_SLOT_LOCK_STALE_S):
        try:
            with open(_LOGIN_SLOT_LAST) as f:
                last = float(f.read().strip() or 0)
        except (OSError, ValueError):
            last = 0.0
        wait_s = gap_s - (time.time() - last)
        if wait_s > 0:
            print("\n[rstudio-local] waiting %.0fs so this login starts %.0fs after the previous one"
                  % (wait_s, gap_s))
            time.sleep(wait_s)
        with open(_LOGIN_SLOT_LAST, "w") as f:
            f.write(repr(time.time()))


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
