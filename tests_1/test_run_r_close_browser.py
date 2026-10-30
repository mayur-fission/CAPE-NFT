"""Start a long-running R script in CLOSE_TAB_SESSION_COUNT new sessions, named
RUN_R_CLOSE_SESSION_<N> (the next free numbers; each
in its own tab), close every tab straight away and check the sessions survive
CLOSE_TAB_TEST_DURATION_S seconds on their own (CLOSE_BROWSER_WAIT_S, the
Jenkins field, wins when set). Meanwhile the session list is checked every
CLOSE_TAB_MONITOR_INTERVAL_S: a session that drops is captured (log line,
screenshot, evidence/session_drops_<label>.json; every check goes to
evidence/session_monitor_<label>.csv) and fails the test. Before the
sessions are quit, each script is stopped (CLOSE_TAB_STOP_SCRIPTS_BEFORE_CLEANUP).
Every session created is force-quit afterwards.

The script (R_SCRIPT_CLOSE_TAB_JOB in .env) must already exist on the
server.
"""
import time

import pytest

from config.config import env, env_path
from common.script_run_scenarios import (
    source_script_close_tabs_then_quit_scenario,
    start_jobs_close_tabs_then_stop_jobs_scenario,
)
from common.session_naming_logic import RUN_R_CLOSE_SESSION_PREFIX

pytestmark = pytest.mark.rstudio_local

SCRIPT_PATH = env_path("R_SCRIPT_CLOSE_TAB_JOB", "RSTUDIO_WORKBENCH_JOB_SCRIPT")
# Number of sessions: CLOSE_TAB_SESSION_COUNT, else the shared RSTUDIO_SESSION_COUNT
SESSION_COUNT = int(env("CLOSE_TAB_SESSION_COUNT") or env("RSTUDIO_SESSION_COUNT", required=True))
# Seconds the sessions run with their tabs closed. CLOSE_BROWSER_WAIT_S (the
# Jenkins field) wins when set - it has its own name because .env overrides
# the environment; then CLOSE_TAB_TEST_DURATION_S, then the shared setting.
TEST_DURATION_SECONDS = float(
    env("CLOSE_BROWSER_WAIT_S") or env("CLOSE_TAB_TEST_DURATION_S")
    or env("RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS", "300")
)
# How often the session list is checked for dropped sessions while the tabs are closed
MONITOR_INTERVAL_S = float(env("CLOSE_TAB_MONITOR_INTERVAL_S", "30"))
# Interrupt each session's R script before the sessions are quit
STOP_SCRIPTS_BEFORE_CLEANUP = env("CLOSE_TAB_STOP_SCRIPTS_BEFORE_CLEANUP", "true").lower() != "false"
# Session names: RUN_R_CLOSE_SESSION_<N> (cleanup quits exactly these)
NAME_PREFIX = RUN_R_CLOSE_SESSION_PREFIX
LABEL = "close_tab_%d_sessions_%s" % (SESSION_COUNT, time.strftime("%Y%m%d-%H%M%S"))


@pytest.mark.skip()
def test_workbench_jobs_keep_running_after_tabs_closed(context):
    """A Workbench job started in each session is still running
    TEST_DURATION_SECONDS after its tab was closed.

    Flow: login -> per session: launch new session in a tab -> start
    SCRIPT_PATH as a Workbench job -> close tab -> wait -> reopen each
    session -> stop its running job -> quit sessions
    """
    failures = start_jobs_close_tabs_then_stop_jobs_scenario(
        context, SESSION_COUNT, SCRIPT_PATH, TEST_DURATION_SECONDS
    )
    assert not failures, "reopen/stop failed after %.0fs: %s" % (TEST_DURATION_SECONDS, failures)


def test_console_scripts_run_after_tabs_closed_then_quit(context):
    """Sessions running a console script (the default loops forever) stay
    Active/Executing without a tab for TEST_DURATION_SECONDS and then quit
    cleanly. A session that drops meanwhile (leaves the list, or turns
    Suspended/Failed/...) is captured and fails the test.

    Flow: login -> per session: launch new session in a tab -> source
    SCRIPT_PATH in the console -> close tab -> watch the session list every
    MONITOR_INTERVAL_S for TEST_DURATION_SECONDS -> reopen each session and
    stop its script -> quit sessions
    """
    source_script_close_tabs_then_quit_scenario(
        context, SESSION_COUNT, SCRIPT_PATH, TEST_DURATION_SECONDS,
        monitor_interval_s=MONITOR_INTERVAL_S, stop_before_cleanup=STOP_SCRIPTS_BEFORE_CLEANUP, label=LABEL,
        name_prefix=NAME_PREFIX,
    )
