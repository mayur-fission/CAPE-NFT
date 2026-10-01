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
)
from common.perf_scenarios import run_launch_perf_single
from common.session_actions import WORKING_DIR, login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_monitoring import keep_sessions_idle_and_poll_file_list
from common.session_scenarios import open_sessions_in_tabs
from common.rstudio_session_helper import next_auto_perf_session_names, row_ids

pytestmark = pytest.mark.rstudio_local

SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))

@pytest.mark.skip()
def test_create_and_quit_one_rstudio_session(page):
    """Creates one session, force-quits it via its own Details panel (never
    "Quit All") and confirms no other session was affected.
    """
    run_launch_perf_single(page)



def test_create_multiple_rstudio_sessions(page, context):
    """Creates SESSION_COUNT sessions one after another, then reopens each in
    its own tab and holds them all idle together, liveness-checking and
    polling the file list every 30s.
    """
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)

    # Reserve every name from one scan: rescanning per session can race the
    # previous row settling and hand out a name already in use.
    planned_names = next_auto_perf_session_names(page, SESSION_COUNT, home_url=home_url)

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
        cleanup_and_verify_sessions(page, home_url, before_ids)
