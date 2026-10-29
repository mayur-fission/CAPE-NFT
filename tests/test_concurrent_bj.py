"""BACKGROUND_SESSIONS sessions (default 5) launched from the UI, named
BACKGROUND_JOB_SESSION_1 .. _<N>, at most 5 tabs per browser (one browser
per 5 sessions, as in the other concurrent tests), all logged in as one
user. Batch execution: each browser starts BACKGROUND_JOB_SCRIPT as an
RStudio background job in all its sessions as soon as its own sessions are
launched - it does not wait for the other browsers. Each job runs for
BACKGROUND_JOB_DURATION seconds (default 600), checked every
BACKGROUND_JOB_CHECK_INTERVAL_S, then is stopped; the sessions are quit
once every browser is done (common/concurrent_background_jobs.py).

A job must keep running for the whole duration (a failed, cancelled or
vanished job fails the test; one that finishes earlier counts as ok) and
must stop when told to. Each job works in
BACKGROUND_JOB_OUTPUT_DIR/<session name> (default OUTPUT_DIR_GENERATE_CSV),
emptied first.

Per-session job times go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time to evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json,
with the session time charts (common/session_time_report.py) next to the
CSV, all attached to Allure.
"""
import posixpath
import time

import pytest

from common.concurrent_background_jobs import run_background_jobs_in_concurrent_sessions
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_naming_logic import BACKGROUND_JOB_SESSION_PREFIX, concurrent_session_names
from common.session_time_report import describe_run, generate_session_time_report
from config.config import env, env_path

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("BACKGROUND_SESSIONS", "5"))
# One browser per 5 sessions: never more than 5 tabs in a browser
BROWSER_COUNT = browsers_for(SESSION_COUNT)
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=BACKGROUND_JOB_SESSION_PREFIX)
SCRIPT_PATH = env_path("BACKGROUND_JOB_SCRIPT")
OUTPUT_DIR = env_path("BACKGROUND_JOB_OUTPUT_DIR", "OUTPUT_DIR_GENERATE_CSV")
# How long each job runs before it is stopped, and how often it is checked.
DURATION_S = float(env("BACKGROUND_JOB_DURATION", "600"))
CHECK_INTERVAL_S = float(env("BACKGROUND_JOB_CHECK_INTERVAL_S", "60"))
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "background_jobs_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))


def test_concurrent_bj(browser_name, browser_type_launch_args, browser_context_args):
    """Every session's background job runs for the duration and stops.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> start the background job in all its tabs
    (without waiting for the other browsers) -> check the jobs until
    BACKGROUND_JOB_DURATION is up -> stop each job -> wait for all
    browsers -> close tabs -> quit created sessions
    """
    assert SESSION_COUNT >= 1, "BACKGROUND_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    records, errors = run_background_jobs_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, OUTPUT_DIR, DURATION_S, check_interval_s=CHECK_INTERVAL_S,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, records)
    summarize_concurrent_runs(records, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Background job run time per session (stopped after %.0fs)" % DURATION_S,
        browser=browser_name, test_name="Batch execution - " + describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script="%s (background job)" % posixpath.basename(SCRIPT_PATH),
    )
    assert not errors, "\n".join(errors)
