"""Idle soak: hold several session tabs open together, checking they stay
alive and polling a folder's file list in each.
"""
import time

from common.config import env
from common.rstudio_console_commands import get_file_list
from common.rstudio_session_helper import is_session_alive

IDLE_MINUTES = env("RSTUDIO_SESSION_IDLE_MINUTES", "5")
FILE_LIST_POLL_INTERVAL_S = env("RSTUDIO_FILE_LIST_POLL_INTERVAL_SECONDS", "30")


def keep_sessions_idle_and_poll_file_list(
    pages, path, duration_minutes=None, interval_s=None, recursive=False, timeout_ms=15000,
):
    """For duration_minutes (default RSTUDIO_SESSION_IDLE_MINUTES), every
    interval_s (default RSTUDIO_FILE_LIST_POLL_INTERVAL_SECONDS) check each
    page's session is alive and list the files in `path`. All pages are
    handled in one loop, since Playwright's sync API can't be shared across
    threads. Polling stops for a page once its session dies.

    Returns (idle_results, file_results, elapsed_s): one {"index", "ok"[,
    "error", "failed_after_s"]} dict per page, and per page a list of
    {"elapsed_s", "files"} snapshots.
    """
    minutes = duration_minutes if duration_minutes is not None else float(IDLE_MINUTES)
    interval_s = interval_s if interval_s is not None else float(FILE_LIST_POLL_INTERVAL_S)

    idle_results = [{"index": i + 1, "ok": True} for i in range(len(pages))]
    file_results = [[] for _ in pages]

    started = time.time()
    deadline = started + minutes * 60
    while time.time() < deadline:
        time.sleep(max(0, min(interval_s, deadline - time.time())))
        elapsed_s = time.time() - started
        for i, page in enumerate(pages):
            if not idle_results[i]["ok"]:
                continue
            if not is_session_alive(page):
                idle_results[i] = {
                    "index": i + 1, "ok": False, "error": "session no longer alive", "failed_after_s": elapsed_s,
                }
                continue
            files = get_file_list(page, path, recursive=recursive, timeout_ms=timeout_ms)
            file_results[i].append({"elapsed_s": elapsed_s, "files": files})
            print(
                "\n[rstudio-local] session %d %s at %.1fs: %d file(s) - %s"
                % (i + 1, path, elapsed_s, len(files), files)
            )
    return idle_results, file_results, time.time() - started
