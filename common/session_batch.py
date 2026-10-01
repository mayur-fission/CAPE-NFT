"""Launch several sessions one after another on a single `page`, retrying
each launch so one failure doesn't lose the rest. For parallel launches
see session_concurrent.py.
"""
import time

from common.config import env
from common.rstudio_session_helper import next_auto_perf_session_names as _next_auto_perf_session_names
from common.rstudio_session_helper import row_ids as _row_ids

from config.perf_report import allure_step

from common.session_actions import launch_session
from common.session_actions import set_session_working_directory
from common.session_scenarios import launch_with_project
from common.session_retry import _LAUNCH_ATTEMPTS
from common.session_retry import _retry_backoff_s


def _launch_one(page, home_url, session_name, index, count):
    """Launch `session_name` (plus working directory), retrying up to
    _LAUNCH_ATTEMPTS times. Returns the per-session result dict, including
    "attempts".
    """
    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with allure_step(
                "launch session %s (%d/%d) (attempt %d)" % (session_name, index, count, attempt + 1)
            ):
                launch = launch_session(page, home_url, session_name=session_name)
                set_session_working_directory(page)
            if attempt:
                print("[rstudio-local] session %s launched on attempt %d/%d"
                      % (session_name, attempt + 1, _LAUNCH_ATTEMPTS))
            return {
                "index": index,
                "ok": True,
                "session_name": session_name,
                "elapsed_s": launch.elapsed_s,
                "bytes_received": launch.bytes_received,
                "attempts": attempt + 1,
            }
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] session %s attempt %d/%d failed: %s"
                  % (session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def create_multiple_sessions(page, home_url, count=None):
    """Launch `count` sessions (default RSTUDIO_SESSION_COUNT) one after
    another, setting each one's working directory. Names are reserved up
    front so none collide. Never raises on a single failure.

    Returns (results, created_ids, run_elapsed_s): one result dict per
    session, the new row ids (one before/after diff), and total seconds.
    """
    if count is None:
        count = int(env("RSTUDIO_SESSION_COUNT", "10"))

    before_ids = _row_ids(page)
    session_names = _next_auto_perf_session_names(page, count, home_url=home_url)
    results = []

    run_started = time.time()
    for i in range(count):
        results.append(
            _launch_one(page, home_url, session_names[i], i + 1, count)
        )
    run_elapsed = time.time() - run_started

    # One diff for the whole batch, taken now that every launch has settled.
    page.goto(home_url)
    page.get_by_text("New Session", exact=True).first.wait_for(state="visible", timeout=30000)
    page.wait_for_timeout(2000)
    created_ids = _row_ids(page) - before_ids

    return results, created_ids, run_elapsed


def _launch_one_with_project(page, home_url, session_name, index, count, working_dir):
    """launch_with_project() for `session_name`, retrying up to
    _LAUNCH_ATTEMPTS times under the same name. Returns the per-session
    result dict.
    """
    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with allure_step(
                "session %s (%d/%d) + project (attempt %d)" % (session_name, index, count, attempt + 1)
            ):
                launch, project = launch_with_project(
                    page, home_url, session_name=session_name, working_dir=working_dir
                )
            if attempt:
                print("[rstudio-local] session %s launched on attempt %d/%d"
                      % (session_name, attempt + 1, _LAUNCH_ATTEMPTS))
            return {
                "index": index,
                "ok": True,
                "session_name": session_name,
                "launch_elapsed_s": launch.elapsed_s,
                "project_elapsed_s": project.elapsed_s,
                "bytes_received": launch.bytes_received,
                "attempts": attempt + 1,
            }
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] session %s attempt %d/%d failed: %s"
                  % (session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def create_multiple_sessions_with_projects(page, home_url, count=None, working_dir=None):
    """Launch `count` sessions one after another, each with a project, so a
    later step can reopen them by name. Names are reserved up front. Never
    raises on a single failure.

    Returns (results, run_elapsed_s).
    """
    if count is None:
        count = int(env("RSTUDIO_SESSION_COUNT", "10"))

    session_names = _next_auto_perf_session_names(page, count, home_url=home_url)

    results = []
    run_started = time.time()
    for i in range(count):
        results.append(
            _launch_one_with_project(
                page, home_url, session_names[i], i + 1, count, working_dir
            )
        )
    run_elapsed = time.time() - run_started

    return results, run_elapsed
