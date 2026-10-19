"""Same as test_concurrent_sessions_30min.py, but the sessions are named
U1_CONCURRENT_30MIN_SESSION_1 .. _<N>, so it can run next to it as the
same user without either cleaning up the other's sessions. It reads the
same CONCURRENT_30MIN_* settings.

Like test_concurrent_sessions.py, but sources the ~30 minute
htp_30min.R. CONCURRENT_30MIN_SESSIONS sessions (default 1) launched
from the UI, named U1_CONCURRENT_30MIN_SESSION_1 .. _<N>, spread over
CONCURRENT_30MIN_SESSIONS_BROWSERS browsers (blank = one browser per 5
sessions), all logged in as one user. Once every browser has launched its
sessions, SCRIPT_PATH is sourced in all of them at the same time and each run
is timed (timeout CONCURRENT_30MIN_SCRIPT_TIMEOUT_MS, default 40 min).

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per session (common/session_time_report.py) next to the CSV, all
attached to Allure along with the screenshots. Set
CONCURRENT_30MIN_THRESHOLD_S to mark a slow-session limit on the charts.
"""
import posixpath
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.result_printing import summarize_concurrent_runs
from common.parallel_browsers import browsers_for
from config.config import env, env_path
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report
from common.session_naming_logic import U1_CONCURRENT_30MIN_SESSION_PREFIX, concurrent_session_names

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("CONCURRENT_30MIN_SESSIONS", "1"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("CONCURRENT_30MIN_SESSIONS_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=U1_CONCURRENT_30MIN_SESSION_PREFIX)
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
SCRIPT_PATH = env_path("R_SCRIPT_CONCURRENT_30MIN", "CONCURRENT_30MIN_SCRIPT")
OUTPUT_DIR = env_path("OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                      "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
# Folder (under the session folder) each script writes its results to; a run
# only counts as ok if it holds at least one file afterwards ("" = no output
# check). CONCURRENT_30MIN_OUTPUT_FILE overrides it.
SCRIPT_OUTPUTS = {
    "htp_10min.R": "pmx_sim_output",
    "htp_30min.R": "pmx_sim_output_30min",
    "generate_10kb_csv.R": "pmx_sim_output_30min",
    "generate_10kb_csv_10min.R": "pmx_sim_output",
}
OUTPUT_FILE_NAME = env("CONCURRENT_30MIN_OUTPUT_FILE", SCRIPT_OUTPUTS.get(posixpath.basename(SCRIPT_PATH), ""))
# The script runs for ~30 minutes; leave headroom on top.
SCRIPT_TIMEOUT_MS = int(env("CONCURRENT_30MIN_SCRIPT_TIMEOUT_MS", "2400000"))
# How often each console is checked for the end of the script; a 30-minute
# run does not need sub-second timing, and fewer checks keep the tabs light.
POLL_INTERVAL_MS = int(env("CONCURRENT_30MIN_POLL_MS", "5000"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "u1_concurrent_30min_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("CONCURRENT_30MIN_THRESHOLD_S") or 0) or None
# Delete each session's output once it has been checked, so it does not pile
# up on the server (set CONCURRENT_30MIN_DELETE_OUTPUT=false to keep it).
DELETE_OUTPUT = env("CONCURRENT_30MIN_DELETE_OUTPUT", "true").lower() != "false"


def test_u1_concurrent_sessions_run_30min_r_script(
    browser_name, browser_type_launch_args, browser_context_args
):
    """The R script finishes ok in every session.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all browsers -> source the script
    in all tabs at once -> wait for all -> check (and delete) each output ->
    close tabs -> quit created sessions
    """
    assert SESSION_COUNT >= 1, "CONCURRENT_30MIN_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    if not OUTPUT_FILE_NAME:
        print("\n[rstudio-local] no output folder set for %s - runs count as ok once the script "
              "finishes; set CONCURRENT_30MIN_OUTPUT_FILE to also check its output" % SCRIPT_PATH)
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=DELETE_OUTPUT, poll_interval_ms=POLL_INTERVAL_MS,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session (30-minute script)", threshold=THRESHOLD_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
