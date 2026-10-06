"""User 2 (RSTUDIO_USER2) creates RSTUDIO_SESSION_COUNT RStudio Pro sessions
named AUTO_SESSION_U2_<N>, so a concurrent user 1 run never collides (see
test_create_and_launch_multiple_sessions_user1.py), and runs an R script in
them. Every test force-quits the sessions it created.
"""
import pytest

from common.config import env
from common.helper_function import (
    launch_sessions_scenario,
    login_and_plan_session_names,
    run_command_concurrently_in_tabs_scenario,
    run_command_in_tabs_scenario,
)

pytestmark = pytest.mark.rstudio_local

USER = 2
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))
SESSION_NAME_PREFIX = "AUTO_SESSION_U2_"
LAUNCH_ATTEMPTS = 3
LABEL = "user2"
SOURCE_COMMAND = env("RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
SCRIPT_TIMEOUT_MS = int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))
MONITOR_POLL_INTERVAL_S = float(env("RSTUDIO_RUN_SCRIPT_MONITOR_POLL_INTERVAL_SECONDS", "2"))


@pytest.mark.skip()
def test_user2_launch_multiple_sessions(page):
    """Launch SESSION_COUNT sessions one after another (each retried up to
    LAUNCH_ATTEMPTS times) and print every launch time.

    Flow: login as user 2 -> launch sessions in turn -> quit sessions
    """
    home_url, before_ids, session_names = login_and_plan_session_names(
        page, SESSION_NAME_PREFIX, SESSION_COUNT, user=USER
    )
    launch_sessions_scenario(page, home_url, before_ids, session_names, LAUNCH_ATTEMPTS, LABEL)


@pytest.mark.skip()
def test_user2_run_r_script_sequentially_in_tabs(page, context):
    """Run SOURCE_COMMAND in every session one after another (total time =
    launches + sum of script times).

    Flow: login as user 2 -> launch sessions -> reopen each in a tab ->
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


def test_user2_run_r_script_concurrently_in_tabs(page, context):
    """Run SOURCE_COMMAND in every session at once (total time = launches +
    slowest script). Fails on any run that errors or exceeds
    SCRIPT_TIMEOUT_MS.

    Flow: login as user 2 -> launch sessions -> submit script in each tab
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
