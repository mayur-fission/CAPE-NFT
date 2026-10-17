"""Like test_run_r_close_browser, but in batches (as
test_concurrent_sessions_batch_execution does): CLOSE_TAB_BATCH_SESSIONS
sessions launched from the UI, named BATCH_RUN_R_CLOSE_SESSION_1 .. BATCH_RUN_R_CLOSE_SESSION_<N>,
spread over CLOSE_TAB_BATCH_BROWSERS browsers (blank = one browser per 5
sessions), all logged in as one user. Each browser, as soon as its own
sessions are launched, sources R_SCRIPT_CLOSE_TAB_JOB in all of them, closes
their tabs and watches them for CLOSE_TAB_TEST_DURATION_S seconds (checking
the session list every CLOSE_TAB_MONITOR_INTERVAL_S) - without waiting for
the other browsers. A session that drops (leaves the list, or turns
Suspended/Failed/...) is captured: log line, screenshot,
evidence/session_drops_<label>.json; every check of each browser goes to
evidence/session_monitor_<label>_<browser>.csv. Once every browser has
finished, each reopens its sessions, stops their scripts
(CLOSE_TAB_STOP_SCRIPTS_BEFORE_CLEANUP) and quits them. Any drop, script that
could not be stopped or browser error fails the test.
"""
import time

import pytest

from common.close_tab_batch import run_close_tab_batch
from common.parallel_browsers import browsers_for
from config.config import env, env_path
from common.session_naming_logic import BATCH_RUN_R_CLOSE_SESSION_PREFIX, concurrent_session_names

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SCRIPT_PATH = env_path("R_SCRIPT_CLOSE_TAB_JOB", "RSTUDIO_WORKBENCH_JOB_SCRIPT")
SESSION_COUNT = int(env("CLOSE_TAB_BATCH_SESSIONS") or env("CLOSE_TAB_SESSION_COUNT")
                    or env("RSTUDIO_SESSION_COUNT", required=True))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("CLOSE_TAB_BATCH_BROWSERS") or 0))
# Own names, so this test cannot quit test_run_r_close_browser's sessions
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=BATCH_RUN_R_CLOSE_SESSION_PREFIX)
# Seconds every browser's sessions run with their tabs closed (CLOSE_BROWSER_WAIT_S,
# the Jenkins field, wins when set), and how often the session list is checked
TEST_DURATION_SECONDS = float(
    env("CLOSE_BROWSER_WAIT_S") or env("CLOSE_TAB_TEST_DURATION_S")
    or env("RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS", "300")
)
MONITOR_INTERVAL_S = float(env("CLOSE_TAB_MONITOR_INTERVAL_S", "30"))
STOP_SCRIPTS_BEFORE_CLEANUP = env("CLOSE_TAB_STOP_SCRIPTS_BEFORE_CLEANUP", "true").lower() != "false"
# Gap between starting one browser and the next, so their logins don't clash
BROWSER_STAGGER_S = int(env("CLOSE_TAB_BATCH_BROWSER_STAGGER_S")
                        or env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
LABEL = "close_tab_batch_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))


def test_run_r_close_browser_batch_execution(browser_name, browser_type_launch_args, browser_context_args):
    """Every session stays Active/Executing with its tab closed for
    TEST_DURATION_SECONDS, and its script can be stopped afterwards.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> source the script in each (without waiting
    for the other browsers) -> close the tabs -> watch the session list ->
    wait for all browsers -> reopen each session and stop its script ->
    quit created sessions
    """
    assert SESSION_COUNT >= 1, "CLOSE_TAB_BATCH_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    drops, stop_failures, errors = run_close_tab_batch(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        LABEL, SCRIPT_PATH, TEST_DURATION_SECONDS, MONITOR_INTERVAL_S,
        stop_before_cleanup=STOP_SCRIPTS_BEFORE_CLEANUP, stagger_s=BROWSER_STAGGER_S,
        attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
    )
    problems = ["dropped while its tab was closed: %s in browser %s (%s after %ds)"
                % (d["session"], d["browser"], d["status"], d["seconds_after_start"]) for d in drops]
    problems += ["script could not be stopped: %s (%s)" % item for item in stop_failures.items()]
    problems += errors
    assert not problems, "\n".join(problems)
