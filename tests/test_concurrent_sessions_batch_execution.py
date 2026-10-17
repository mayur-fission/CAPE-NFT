"""Like test_concurrent_sessions, but in batches: CONCURRENT_BATCH_SESSIONS
sessions launched from the UI, named CONCURRENT_BATCH_1 .. CONCURRENT_BATCH_<N>,
spread over CONCURRENT_BATCH_SESSIONS_BROWSERS browsers (blank = one browser
per 5 sessions), all logged in as one user. Each browser starts SCRIPT_PATH
in all its sessions as soon as its own sessions are launched - it does not
wait for the other browsers - then waits for its runs and checks (and
deletes) each output. The tabs are closed and the sessions quit only once
every browser has finished, so no cleanup runs while another browser is
still executing.

Shares the other CONCURRENT_SESSIONS_* settings (script, output folder,
timeouts, browser stagger, ...) with test_concurrent_sessions. Per-session
timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv, the
overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with the session
time charts next to the CSV, all attached to Allure.
"""
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.result_printing import summarize_concurrent_runs
from common.parallel_browsers import browsers_for
from config.config import env, env_path
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report
from common.session_naming_logic import CONCURRENT_BATCH_SESSION_PREFIX, concurrent_session_names

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("CONCURRENT_BATCH_SESSIONS") or env("CONCURRENT_SESSIONS", "10"))
BROWSER_COUNT = browsers_for(
    SESSION_COUNT, int(env("CONCURRENT_BATCH_SESSIONS_BROWSERS") or env("CONCURRENT_SESSIONS_BROWSERS") or 0)
)
# Own names, so this test cannot quit test_concurrent_sessions' sessions
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=CONCURRENT_BATCH_SESSION_PREFIX)
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
SCRIPT_PATH = env_path("R_SCRIPT_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_SCRIPT",
                       "R_SCRIPT_GENERATE_CSV", "CSV_SESSIONS_SCRIPT")
OUTPUT_DIR = env_path("OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                      "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
OUTPUT_FILE_NAME = env("CONCURRENT_SESSIONS_OUTPUT_FILE", "generated_data_10kb.csv")
SCRIPT_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_SCRIPT_TIMEOUT_MS", "600000"))
# Run start time in the name: every run (and this test running next to
# test_concurrent_sessions) writes its own CSV, charts and summary
TIMINGS_LABEL = "concurrent_batch_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("CONCURRENT_SESSIONS_THRESHOLD_S") or 0) or None
DELETE_OUTPUT = env("CONCURRENT_SESSIONS_DELETE_OUTPUT", "true").lower() != "false"


def test_concurrent_sessions_batch_execution(browser_name, browser_type_launch_args, browser_context_args):
    """The R script finishes ok in every session.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> source the script in all its tabs at once
    (without waiting for the other browsers) -> wait for its runs -> check
    (and delete) each output -> wait for all browsers -> close tabs -> quit
    created sessions
    """
    assert SESSION_COUNT >= 1, "CONCURRENT_BATCH_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=DELETE_OUTPUT, start_together=False,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, threshold=THRESHOLD_S, browser=browser_name,
        test_name="Batch execution - " + describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
