"""Launch several sessions one after another on a single `page`, retrying each
launch so one failure doesn't lose the rest. For parallel launches see
session_concurrent.py.
"""
import time

from config.perf_report import allure_step

from common.config import default_session_count
from common.rstudio_session_helper import goto_session_list, next_auto_perf_session_names, row_ids
from common.session_actions import launch_session, set_session_working_directory
from common.session_retry import result_with_retries
from common.session_scenarios import launch_with_project


def _launch_in_turn(page, home_url, count, launch_one):
    """Reserve `count` AUTO_PERF_SESSION_<N> names and run
    launch_one(session_name, index, attempt) for each, with retries.
    Returns (results, run_elapsed_s).
    """
    session_names = next_auto_perf_session_names(page, count, home_url=home_url)
    run_started = time.time()
    results = [
        result_with_retries(i + 1, name, lambda attempt, name=name, i=i: launch_one(name, i + 1, attempt))
        for i, name in enumerate(session_names)
    ]
    return results, time.time() - run_started


def create_multiple_sessions(page, home_url, count=None):
    """Launch `count` sessions (default RSTUDIO_SESSION_COUNT) one after
    another, setting each one's working directory. Never raises on a single
    failure.

    Returns (results, created_ids, run_elapsed_s): one result dict per
    session ("elapsed_s", "bytes_received" on success), the new session-list
    row ids (one before/after diff) and total seconds.
    """
    count = count or default_session_count()
    before_ids = row_ids(page)

    def _launch(session_name, index, attempt):
        with allure_step("launch session %s (%d/%d) (attempt %d)" % (session_name, index, count, attempt)):
            launch = launch_session(page, home_url, session_name=session_name)
            set_session_working_directory(page)
        return {"elapsed_s": launch.elapsed_s, "bytes_received": launch.bytes_received}

    results, run_elapsed = _launch_in_turn(page, home_url, count, _launch)

    # One diff for the whole batch, taken now that every launch has settled.
    goto_session_list(page, home_url, settle_ms=2000)
    return results, row_ids(page) - before_ids, run_elapsed


def create_multiple_sessions_with_projects(page, home_url, count=None, working_dir=None):
    """Launch `count` sessions (default RSTUDIO_SESSION_COUNT) one after
    another, each with a project, so a later step can reopen them by name.
    Never raises on a single failure.

    Returns (results, run_elapsed_s); results carry "launch_elapsed_s",
    "project_elapsed_s" and "bytes_received" on success.
    """
    count = count or default_session_count()

    def _launch(session_name, index, attempt):
        with allure_step("session %s (%d/%d) + project (attempt %d)" % (session_name, index, count, attempt)):
            launch, project = launch_with_project(page, home_url, session_name=session_name, working_dir=working_dir)
        return {
            "launch_elapsed_s": launch.elapsed_s,
            "project_elapsed_s": project.elapsed_s,
            "bytes_received": launch.bytes_received,
        }

    return _launch_in_turn(page, home_url, count, _launch)
