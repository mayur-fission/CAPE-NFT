"""Like test_concurrent_sessions.py, but sources the small ~10 minute
pmx_10kb_10min.R (R_SCRIPT_SMALL_10MIN). CONCURRENT_SMALL_10MIN_SESSIONS
sessions (default 1) launched from the UI, named
CONCURRENT_SMALL_10MIN_SESSION_1 .. _<N>, spread over
CONCURRENT_SMALL_10MIN_SESSIONS_BROWSERS browsers (blank = one browser per 5
sessions), all logged in as one user. Once every browser has launched its
sessions, SCRIPT_PATH is sourced in all of them at the same time and each run
is timed (timeout CONCURRENT_SMALL_10MIN_SCRIPT_TIMEOUT_MS, default 15 min).

Each session runs the script in its own folder, <output dir>/<session name>/,
where it writes pmx_10kb_output/; a run only counts as ok if all of
OUTPUT_FILES are in there afterwards: data/nm_dataset.csv (the ~10 KB
NONMEM-style dataset), tables/exposure_summary.csv and logs/run_log.txt,
model_spec.txt, session_info.txt and run_summary.txt. pmx_10kb_output/ is
then deleted (CONCURRENT_SMALL_10MIN_DELETE_OUTPUT=false keeps it); a failed
check keeps it, to look into.

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per session (common/session_time_report.py) next to the CSV, all
attached to Allure along with the screenshots. Set
CONCURRENT_SMALL_10MIN_THRESHOLD_S to mark a slow-session limit on the charts.
"""
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.result_printing import summarize_concurrent_runs
from common.parallel_browsers import browsers_for
from config.config import env, env_list, env_path
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report
from common.session_naming_logic import CONCURRENT_SMALL_10MIN_SESSION_PREFIX, concurrent_session_names

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("CONCURRENT_SMALL_10MIN_SESSIONS", "1"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("CONCURRENT_SMALL_10MIN_SESSIONS_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=CONCURRENT_SMALL_10MIN_SESSION_PREFIX)
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATH = env("R_SCRIPT_SMALL_10MIN")
OUTPUT_DIR = env_path("OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                      "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
# Files the script writes in pmx_10kb_output/ under its session folder; all
# must exist for a run to count as ok. CONCURRENT_SMALL_10MIN_OUTPUT_FILES
# (comma-separated, relative to the session folder) overrides.
SCRIPT_OUTPUT_FOLDER = "pmx_10kb_output"
OUTPUT_FILES = env_list("CONCURRENT_SMALL_10MIN_OUTPUT_FILES", [
    "%s/%s" % (SCRIPT_OUTPUT_FOLDER, path) for path in (
        "data/nm_dataset.csv",
        "tables/exposure_summary.csv",
        "logs/run_log.txt",
        "logs/model_spec.txt",
        "logs/session_info.txt",
        "logs/run_summary.txt",
    )
])
# The script runs for ~10 minutes; leave headroom on top.
SCRIPT_TIMEOUT_MS = int(env("CONCURRENT_SMALL_10MIN_SCRIPT_TIMEOUT_MS", "900000"))
# How often each console is checked for the end of the script.
POLL_INTERVAL_MS = int(env("CONCURRENT_SMALL_10MIN_POLL_MS", "500"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "concurrent_small_10min_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("CONCURRENT_SMALL_10MIN_THRESHOLD_S") or 0) or None
# Delete each session's output once it has been checked, so it does not pile
# up on the server (set CONCURRENT_SMALL_10MIN_DELETE_OUTPUT=false to keep it).
DELETE_OUTPUT = env("CONCURRENT_SMALL_10MIN_DELETE_OUTPUT", "true").lower() != "false"


def test_concurrent_sessions_run_small_10min_r_script(
    browser_name, browser_type_launch_args, browser_context_args
):
    """The R script finishes ok, with all its output files, in every session.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all browsers -> source the script
    in all tabs at once, each in its own folder -> wait for all -> check (and
    delete) each session's output files -> close tabs -> quit created sessions
    """
    if not SCRIPT_PATH:
        pytest.skip("R_SCRIPT_SMALL_10MIN is not set - set it in .env (on Jenkins: in the environment's .env "
                    "credential) to the path of pmx_10kb_10min.R")
    assert SESSION_COUNT >= 1, "CONCURRENT_SMALL_10MIN_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILES, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=DELETE_OUTPUT, poll_interval_ms=POLL_INTERVAL_MS,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session (small 10-minute script)", threshold=THRESHOLD_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
