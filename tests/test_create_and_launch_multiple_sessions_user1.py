"""Functional check: multiple RStudio Pro sessions can be created and
launched as user 1 (RSTUDIO_USER1), each named AUTO_SESSION_U1_<N> so a
concurrent user1/user2 run never collides (see tests/test_two_user_login.py).
Every test force-quits the sessions it created.
"""
import pytest

from common.config import env
from common.helper_function import (
    launch_sessions_scenario,
    run_command_concurrently_in_tabs_scenario,
    run_command_in_tabs_scenario,
)
from common.session_actions import login_to_posit_workbench
from common.rstudio_session_helper import next_session_names, row_ids

pytestmark = pytest.mark.rstudio_local

SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))
SESSION_NAME_PREFIX = "AUTO_SESSION_U1_"
LAUNCH_ATTEMPTS = 3
LABEL = "user1"
SOURCE_COMMAND = env("RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND", "source('batch_mayur/sample_10mb.R')")
SCRIPT_TIMEOUT_MS = int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))
MONITOR_POLL_INTERVAL_S = float(env("RSTUDIO_RUN_SCRIPT_MONITOR_POLL_INTERVAL_SECONDS", "2"))

@pytest.mark.skip()
def test_create_multiple_session_and_launch_user1(page):
    home_url = login_to_posit_workbench(page, user=1)
    before_ids = row_ids(page)
    session_names = next_session_names(page, SESSION_NAME_PREFIX, SESSION_COUNT, home_url=home_url)

    launch_sessions_scenario(page, home_url, before_ids, session_names, LAUNCH_ATTEMPTS, LABEL)

@pytest.mark.skip()
def test_create_multiple_sessions_and_run_r_script_in_tabs_user1(page, context):
    """Launches SESSION_COUNT sessions, reopens each in a tab and runs
    SOURCE_COMMAND in each tab one after another (total = launches + sum of
    script times).
    """
    home_url = login_to_posit_workbench(page, user=1)
    before_ids = row_ids(page)
    session_names = next_session_names(page, SESSION_NAME_PREFIX, SESSION_COUNT, home_url=home_url)

    open_failures = run_command_in_tabs_scenario(
        page, context, home_url, before_ids, session_names,
        LAUNCH_ATTEMPTS, SOURCE_COMMAND, SCRIPT_TIMEOUT_MS, LABEL,
    )
    assert not open_failures, "sessions that failed to reopen in a tab: %s" % open_failures


def test_create_multiple_sessions_and_run_r_scripts_concurrently_user1(page, context):
    """Launches SESSION_COUNT sessions, then submits SOURCE_COMMAND in each
    session's tab without waiting and monitors all runs together (total =
    launches + slowest script). Fails on any script that errors or exceeds
    SCRIPT_TIMEOUT_MS.
    """
    home_url = login_to_posit_workbench(page, user=1)
    before_ids = row_ids(page)
    session_names = next_session_names(page, SESSION_NAME_PREFIX, SESSION_COUNT, home_url=home_url)

    unfinished = run_command_concurrently_in_tabs_scenario(
        page, context, home_url, before_ids, session_names,
        LAUNCH_ATTEMPTS, SOURCE_COMMAND, SCRIPT_TIMEOUT_MS, MONITOR_POLL_INTERVAL_S, LABEL,
    )
    assert not unfinished, (
        "sessions whose script did not finish within %dms: %s" % (SCRIPT_TIMEOUT_MS, unfinished)
    )
