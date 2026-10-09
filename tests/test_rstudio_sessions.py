"""Functional checks for RStudio Pro sessions on a local Posit Workbench via
the real browser flow. Every test force-quits the sessions it created.
"""
import pytest

from common.config import env
from common.helper_function import (
    close_tabs,
    create_sessions_with_working_dir,
    failed,
    format_timings,
    login_and_plan_session_names,
)
from common.perf_scenarios import run_launch_perf_single
from common.rstudio_session_helper import AUTO_PERF_NAME_PREFIX
from common.session_actions import WORKING_DIR
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_monitoring import keep_sessions_idle_and_poll_file_list
from common.session_scenarios import open_sessions_in_tabs

pytestmark = pytest.mark.rstudio_local

SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))


@pytest.mark.skip()
def test_create_and_quit_single_session(page):
    """One session is created and then force-quit through its own Details
    panel (never "Quit All"), leaving every other session untouched.

    Flow: login -> launch new session -> quit session -> check no other
    session was affected
    """
    run_launch_perf_single(page)


def test_multiple_sessions_stay_alive_while_idle(page, context):
    """SESSION_COUNT sessions all stay alive while held idle together in
    their own tabs, with liveness checks and a file-list poll every 30s.

    Flow: login -> reserve AUTO_PERF_SESSION_<N> names -> launch each session
    and setwd -> reopen each in a tab -> hold idle and poll -> close tabs ->
    quit sessions
    """
    # Reserve every name from one scan: rescanning per session can race the
    # previous row settling and hand out a name already in use.
    home_url, before_ids, planned_names = login_and_plan_session_names(
        page, AUTO_PERF_NAME_PREFIX, SESSION_COUNT
    )

    tabs = []
    try:
        timings, session_names, failures = create_sessions_with_working_dir(page, home_url, planned_names)

        assert not failures, "sessions that failed to load: %s" % failures
        assert len(timings) == SESSION_COUNT, (
            "only %d/%d sessions opened successfully" % (len(timings), SESSION_COUNT)
        )
        print(
            "\n[rstudio-local] opened %d sessions; per-session create time (s): %s"
            % (SESSION_COUNT, format_timings(timings))
        )

        tabs, open_results = open_sessions_in_tabs(context, home_url, session_names)
        open_failures = failed(open_results)
        assert not open_failures, "sessions that failed to reopen in a tab: %s" % open_failures

        idle_results, file_results, idle_elapsed = keep_sessions_idle_and_poll_file_list(tabs, WORKING_DIR)
        idle_failures = failed(idle_results)
        assert not idle_failures, "sessions that died while idle: %s" % idle_failures
        print(
            "\n[rstudio-local] held %d sessions idle for %.1fs, polling %s every 30s - "
            "all stayed alive (%s poll(s) each)"
            % (len(tabs), idle_elapsed, WORKING_DIR, len(file_results[0]) if file_results else 0)
        )
    finally:
        close_tabs(tabs)
        cleanup_and_verify_sessions(page, home_url, before_ids, session_names=planned_names)
