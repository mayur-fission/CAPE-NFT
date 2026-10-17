"""CONCURRENT_SESSIONS sessions launched from the UI, named
CONCURRENT_SESSION_1 .. CONCURRENT_SESSION_<N>, spread over
CONCURRENT_SESSIONS_BROWSERS browsers (blank = one browser per 5 sessions;
keep it to 5-6 tabs per browser), all logged in as one user. Once every
browser has launched its sessions, SCRIPT_PATH is sourced in all of them at
the same time and each run is timed.

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per session (common/session_time_report.py) next to the CSV, all
attached to Allure along with the screenshots. Set
CONCURRENT_SESSIONS_THRESHOLD_S to mark a slow-session limit on the charts.
"""
import time

import pytest

from common.concurrent_sessions import (
    browsers_for,
    concurrent_session_names,
    run_script_in_concurrent_sessions,
    summarize_concurrent_runs,
)
from common.config import env, env_path
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("CONCURRENT_SESSIONS", "10"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("CONCURRENT_SESSIONS_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT)
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
# Run start time in the name: every run (and this test running next to the batch
# test) writes its own CSV, charts and summary instead of overwriting another's
TIMINGS_LABEL = "concurrent_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("CONCURRENT_SESSIONS_THRESHOLD_S") or 0) or None
# Delete each session's output once it has been checked, so it does not pile
# up on the server (set CONCURRENT_SESSIONS_DELETE_OUTPUT=false to keep it).
DELETE_OUTPUT = env("CONCURRENT_SESSIONS_DELETE_OUTPUT", "true").lower() != "false"


def test_concurrent_sessions_run_r_script_in_multiple_browsers(
    browser_name, browser_type_launch_args, browser_context_args
):
    """The R script finishes ok in every session.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all browsers -> source the script
    in all tabs at once -> wait for all -> check (and delete) each output ->
    close tabs -> quit created sessions
    """
    assert SESSION_COUNT >= 1, "CONCURRENT_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=DELETE_OUTPUT,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, threshold=THRESHOLD_S, browser=browser_name,
        test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        notes=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
