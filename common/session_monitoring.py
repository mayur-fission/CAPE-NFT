"""Soak checks: hold sessions open or idle for a duration, optionally
polling their file lists meanwhile.
"""
import time

from common.config import env
from common.rstudio_console_commands import get_file_list as _get_file_list
from common.rstudio_session_helper import is_session_alive as _is_session_alive

IDLE_MINUTES = env("RSTUDIO_SESSION_IDLE_MINUTES", "5")
FILE_LIST_POLL_INTERVAL_S = env("RSTUDIO_FILE_LIST_POLL_INTERVAL_SECONDS", "30")


def _resolve_duration_s(duration_s, duration_minutes, caller_name):
    """Duration in seconds from duration_s or duration_minutes; raises
    ValueError if neither is given.
    """
    if duration_s is not None:
        return duration_s
    if duration_minutes is not None:
        return duration_minutes * 60
    raise ValueError("%s() needs duration_s or duration_minutes" % caller_name)


def keep_session_open(page, duration_s=None, duration_minutes=None, check_interval_s=30):
    """Hold `page`'s session open for the given duration (one of duration_s/
    duration_minutes), checking it's alive every check_interval_s.

    Returns the elapsed seconds. Raises RuntimeError if the session dies.
    """
    duration_s = _resolve_duration_s(duration_s, duration_minutes, "keep_session_open")

    started = time.time()
    deadline = started + duration_s
    while time.time() < deadline:
        wait_s = min(check_interval_s, deadline - time.time())
        page.wait_for_timeout(wait_s * 1000)
        if not _is_session_alive(page):
            raise RuntimeError(
                "session was no longer alive after %.1fs of the requested %.1fs"
                % (time.time() - started, duration_s)
            )
    return time.time() - started


def keep_sessions_open(pages, duration_s=None, duration_minutes=None, check_interval_s=30):
    """keep_session_open() for several pages at once. A page that dies is
    recorded, not raised.

    Returns (results, run_elapsed_s), one {"index", "ok"[, "error",
    "failed_after_s"]} dict per page.
    """
    duration_s = _resolve_duration_s(duration_s, duration_minutes, "keep_sessions_open")

    results = [{"index": i + 1, "ok": True} for i in range(len(pages))]

    started = time.time()
    deadline = started + duration_s
    while time.time() < deadline:
        wait_s = min(check_interval_s, deadline - time.time())
        time.sleep(wait_s)
        for i, page in enumerate(pages):
            if not results[i]["ok"]:
                continue
            if not _is_session_alive(page):
                results[i] = {
                    "index": i + 1,
                    "ok": False,
                    "error": "session no longer alive",
                    "failed_after_s": time.time() - started,
                }
    return results, time.time() - started


def keep_session_idle(page, duration_minutes=None, check_interval_s=30):
    """keep_session_open() for duration_minutes (default
    RSTUDIO_SESSION_IDLE_MINUTES).
    """
    minutes = duration_minutes if duration_minutes is not None else float(IDLE_MINUTES)
    return keep_session_open(page, duration_minutes=minutes, check_interval_s=check_interval_s)


def keep_sessions_idle(pages, duration_minutes=None, check_interval_s=30):
    """keep_sessions_open() for duration_minutes (default
    RSTUDIO_SESSION_IDLE_MINUTES).
    """
    minutes = duration_minutes if duration_minutes is not None else float(IDLE_MINUTES)
    return keep_sessions_open(pages, duration_minutes=minutes, check_interval_s=check_interval_s)


def poll_session_file_list(
    page, path, duration_s=None, duration_minutes=None, interval_s=None,
    recursive=False, timeout_ms=15000,
):
    """List the files in `path` every interval_s (default
    RSTUDIO_FILE_LIST_POLL_INTERVAL_SECONDS) for the given duration.

    Returns one {"elapsed_s", "files"} snapshot per poll.
    """
    duration_s = _resolve_duration_s(duration_s, duration_minutes, "poll_session_file_list")
    interval_s = interval_s if interval_s is not None else float(FILE_LIST_POLL_INTERVAL_S)

    snapshots = []
    started = time.time()
    deadline = started + duration_s
    while time.time() < deadline:
        wait_s = min(interval_s, deadline - time.time())
        page.wait_for_timeout(wait_s * 1000)
        elapsed_s = time.time() - started
        files = _get_file_list(page, path, recursive=recursive, timeout_ms=timeout_ms)
        snapshots.append({"elapsed_s": elapsed_s, "files": files})
        print("\n[rstudio-local] %s at %.1fs: %d file(s) - %s" % (path, elapsed_s, len(files), files))
    return snapshots


def keep_sessions_idle_and_poll_file_list(
    pages, path, duration_minutes=None, interval_s=None, recursive=False, timeout_ms=15000,
):
    """keep_sessions_idle() and poll_session_file_list() for every page in one
    loop (Playwright's sync API can't be driven from several threads).

    Returns (idle_results, file_results, elapsed_s). Polling stops for a page
    once it dies.
    """
    minutes = duration_minutes if duration_minutes is not None else float(IDLE_MINUTES)
    duration_s = minutes * 60
    interval_s = interval_s if interval_s is not None else float(FILE_LIST_POLL_INTERVAL_S)

    idle_results = [{"index": i + 1, "ok": True} for i in range(len(pages))]
    file_results = [[] for _ in pages]

    started = time.time()
    deadline = started + duration_s
    while time.time() < deadline:
        wait_s = min(interval_s, deadline - time.time())
        time.sleep(wait_s)
        elapsed_s = time.time() - started
        for i, page in enumerate(pages):
            if not idle_results[i]["ok"]:
                continue
            if not _is_session_alive(page):
                idle_results[i] = {
                    "index": i + 1,
                    "ok": False,
                    "error": "session no longer alive",
                    "failed_after_s": elapsed_s,
                }
                continue
            files = _get_file_list(page, path, recursive=recursive, timeout_ms=timeout_ms)
            file_results[i].append({"elapsed_s": elapsed_s, "files": files})
            print(
                "\n[rstudio-local] session %d %s at %.1fs: %d file(s) - %s"
                % (i + 1, path, elapsed_s, len(files), files)
            )
    return idle_results, file_results, time.time() - started
