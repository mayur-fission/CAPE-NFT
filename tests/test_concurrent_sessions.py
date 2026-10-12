"""CONCURRENT_SESSIONS sessions launched from the UI, named
CONCURRENT_SESSION_1 .. CONCURRENT_SESSION_<N>, spread over
CONCURRENT_SESSIONS_BROWSERS browsers (blank = one browser per 5 sessions;
keep it to 5-6 tabs per browser), all logged in as one user. Once every
browser has launched its sessions, SCRIPT_PATH is sourced in all of them at
the same time and each run is timed.

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, both attached to
Allure along with the screenshots.
"""
import pytest

from common.concurrent_sessions import (
    browsers_for,
    concurrent_session_names,
    run_script_in_concurrent_sessions,
    summarize_concurrent_runs,
)
from common.config import env
from common.script_timings import script_timings_csv_path, write_script_timings_csv

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
SCRIPT_PATH = env("CONCURRENT_SESSIONS_SCRIPT", env("CSV_SESSIONS_SCRIPT", "/home/posit/batch_mayur/generate_10kb_csv.R"))
OUTPUT_DIR = env("CONCURRENT_SESSIONS_OUTPUT_DIR", env("CSV_SESSIONS_OUTPUT_DIR", "/home/posit"))
OUTPUT_FILE_NAME = env("CONCURRENT_SESSIONS_OUTPUT_FILE", "generated_data_10kb.csv")
SCRIPT_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_SCRIPT_TIMEOUT_MS", "600000"))
TIMINGS_LABEL = "concurrent_%d_sessions_%d_browsers_U%d" % (SESSION_COUNT, BROWSER_COUNT, USER)


def test_concurrent_sessions_run_r_script_in_multiple_browsers(
    browser_name, browser_type_launch_args, browser_context_args
):
    """The R script finishes ok in every session.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all browsers -> source the script
    in all tabs at once -> wait for all -> close tabs -> quit created sessions
    """
    assert SESSION_COUNT >= 1, "CONCURRENT_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S,
    )
    write_script_timings_csv(script_timings_csv_path(TIMINGS_LABEL), runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    assert not errors, "\n".join(errors)
