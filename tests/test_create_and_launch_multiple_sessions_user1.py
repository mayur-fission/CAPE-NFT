"""User 1 (RSTUDIO_USER1) creates RSTUDIO_SESSION_COUNT RStudio Pro sessions
named AUTO_SESSION_U1_<N>, so a concurrent user 2 run never collides (see
test_create_and_launch_multiple_sessions_user2.py), and runs an R script in
them. Every test force-quits the sessions it created.
"""
import time

import pytest

from common.config import env
from common.helper_function import (
    launch_sessions_scenario,
    login_and_plan_session_names,
    run_command_concurrently_in_tabs_scenario,
    run_command_in_tabs_scenario,
)

pytestmark = pytest.mark.rstudio_local

USER = 1
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))
SESSION_NAME_PREFIX = "AUTO_SESSION_U1_"
LAUNCH_ATTEMPTS = 3
LABEL = "user1"
SOURCE_COMMAND = env("RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
SCRIPT_TIMEOUT_MS = int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))
MONITOR_POLL_INTERVAL_S = float(env("RSTUDIO_RUN_SCRIPT_MONITOR_POLL_INTERVAL_SECONDS", "2"))


@pytest.mark.skip()
def test_user1_launch_multiple_sessions(page):
    """Launch SESSION_COUNT sessions one after another (each retried up to
    LAUNCH_ATTEMPTS times) and print every launch time.

    Flow: login as user 1 -> launch sessions in turn -> quit sessions
    """
    home_url, before_ids, session_names = login_and_plan_session_names(
        page, SESSION_NAME_PREFIX, SESSION_COUNT, user=USER
    )
    launch_sessions_scenario(page, home_url, before_ids, session_names, LAUNCH_ATTEMPTS, LABEL)


@pytest.mark.skip()
def test_user1_run_r_script_sequentially_in_tabs(page, context):
    """Run SOURCE_COMMAND in every session one after another (total time =
    launches + sum of script times).

    Flow: login as user 1 -> launch sessions -> reopen each in a tab ->
    run script in each tab in turn -> close tabs -> quit sessions
    """
    home_url, before_ids, session_names = login_and_plan_session_names(
        page, SESSION_NAME_PREFIX, SESSION_COUNT, user=USER
    )
    open_failures = run_command_in_tabs_scenario(
        page, context, home_url, before_ids, session_names,
        LAUNCH_ATTEMPTS, SOURCE_COMMAND, SCRIPT_TIMEOUT_MS, LABEL,
    )
    assert not open_failures, "sessions that failed to reopen in a tab: %s" % open_failures


def test_user1_run_r_script_concurrently_in_tabs(page, context):
    """Run SOURCE_COMMAND in every session at once (total time = launches +
    slowest script). Fails on any run that errors or exceeds
    SCRIPT_TIMEOUT_MS.

    Flow: login as user 1 -> launch sessions -> submit script in each tab
    without waiting -> monitor all runs together -> close tabs -> quit sessions
    """
    home_url, before_ids, session_names = login_and_plan_session_names(
        page, SESSION_NAME_PREFIX, SESSION_COUNT, user=USER
    )
    unfinished = run_command_concurrently_in_tabs_scenario(
        page, context, home_url, before_ids, session_names,
        LAUNCH_ATTEMPTS, SOURCE_COMMAND, SCRIPT_TIMEOUT_MS, MONITOR_POLL_INTERVAL_S, LABEL,
    )
    assert not unfinished, (
        "sessions whose script did not finish within %dms: %s" % (SCRIPT_TIMEOUT_MS, unfinished)
    )


def test_user1_run_r_script_concurrently_in_tabs_total_time(page, context):
    """Same as test_user1_run_r_script_concurrently_in_tabs, but also prints
    the wall-clock time of the whole run, login and cleanup included, even
    when the run fails.

    Flow: start timer -> login as user 1 -> launch sessions -> run script in
    every tab at once -> close tabs -> quit sessions -> print total time
    """
    started = time.time()
    unfinished = None
    try:
        home_url, before_ids, session_names = login_and_plan_session_names(
            page, SESSION_NAME_PREFIX, SESSION_COUNT, user=USER
        )
        unfinished = run_command_concurrently_in_tabs_scenario(
            page, context, home_url, before_ids, session_names,
            LAUNCH_ATTEMPTS, SOURCE_COMMAND, SCRIPT_TIMEOUT_MS, MONITOR_POLL_INTERVAL_S, LABEL,
        )
    finally:
        print(
            "\n[rstudio-local] %s: complete run of %d sessions with %r took %.2fs (%s)"
            % (LABEL, SESSION_COUNT, SOURCE_COMMAND, time.time() - started,
               "passed" if unfinished == [] else "failed")
        )
    assert not unfinished, (
        "sessions whose script did not finish within %dms: %s" % (SCRIPT_TIMEOUT_MS, unfinished)
    )
