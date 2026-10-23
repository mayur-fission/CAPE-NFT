"""Like test_concurrent_sessions_10min_s.py, but sources the nlmixr2
SSE script nlmixr2_sse_10min.R (NLMIXR_SCRIPT). NLMIXR_10MIN_SESSIONS
sessions (default 1) launched from the UI, named NLMIXR_10MIN_SESSION_1 ..
_<N>, spread over NLMIXR_10MIN_SESSIONS_BROWSERS browsers (blank = one
browser per 5 sessions), all logged in as one user. Once every browser has
launched its sessions, SCRIPT_PATH is sourced in all of them at the same time
and each run is timed (timeout NLMIXR_10MIN_SCRIPT_TIMEOUT_MS, default 15 min).

First the R packages the script needs (NLMIXR_10MIN_R_PACKAGES) are checked, and
any missing ones installed into the user's library, from a setup session of
their own (NLMIXR_10MIN_SESSION_SETUP), so the timed runs don't include the install.

Each session runs the script in its own folder, <output dir>/<session name>/.
Set NLMIXR_10MIN_OUTPUT_FILES (comma-separated, relative to the session
folder) to the files the script writes, and a run only counts as ok if all of
them are there afterwards (then deleted, unless
NLMIXR_10MIN_DELETE_OUTPUT=false); unset, only the script finishing without
an error is checked. NLMIXR_10MIN_RUN_SUMMARY names a run summary file in the
pmx scripts' format (replicates, runtime, stop reason) to read into the
results (unset = none).

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per session (common/session_time_report.py) next to the CSV, all
attached to Allure along with the screenshots. Set
NLMIXR_10MIN_THRESHOLD_S to mark a slow-session limit on the charts.
"""
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.result_printing import summarize_concurrent_runs
from common.parallel_browsers import browsers_for
from common.r_packages import NLMIXR_PACKAGES, install_missing_r_packages
from config.config import env, env_list, env_path
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report
from common.session_naming_logic import NLMIXR_10MIN_SESSION_PREFIX, concurrent_session_names

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("NLMIXR_10MIN_SESSIONS", "1"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("NLMIXR_10MIN_SESSIONS_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=NLMIXR_10MIN_SESSION_PREFIX)
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATH = env("NLMIXR_SCRIPT")
# R packages the script needs; the missing ones are installed before it runs.
# NLMIXR_10MIN_R_PACKAGES (comma-separated) overrides ("none" = no check).
R_PACKAGES = [p for p in env_list("NLMIXR_10MIN_R_PACKAGES", NLMIXR_PACKAGES) if p.lower() != "none"]
OUTPUT_DIR = env_path("OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                      "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
# Files the script writes under its session folder; all must exist for a run
# to count as ok. Unset = no output check.
OUTPUT_FILES = env_list("NLMIXR_10MIN_OUTPUT_FILES")
RUN_SUMMARY_FILE = env("NLMIXR_10MIN_RUN_SUMMARY", "")
# The script runs for ~10 minutes; leave headroom on top.
SCRIPT_TIMEOUT_MS = int(env("NLMIXR_10MIN_SCRIPT_TIMEOUT_MS", "900000"))
# How often each console is checked for the end of the script.
POLL_INTERVAL_MS = int(env("NLMIXR_10MIN_POLL_MS", "500"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "nlmixr_10min_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("NLMIXR_10MIN_THRESHOLD_S") or 0) or None
# Delete each session's output once it has been checked, so it does not pile
# up on the server (set NLMIXR_10MIN_DELETE_OUTPUT=false to keep it).
DELETE_OUTPUT = env("NLMIXR_10MIN_DELETE_OUTPUT", "true").lower() != "false"


def test_nlmixr_10min_r_script(browser_name, browser_type_launch_args, browser_context_args):
    """The nlmixr2 R script finishes ok (with its output files, when
    NLMIXR_10MIN_OUTPUT_FILES is set) in every session.

    Flow: check the R packages, installing missing ones (setup session) ->
    per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all browsers -> source the script
    in all tabs at once, each in its own folder -> wait for all -> check (and
    delete) each session's output files -> close tabs -> quit created sessions
    """
    if not SCRIPT_PATH:
        pytest.skip("NLMIXR_SCRIPT is not set - set it in .env (on Jenkins: in the environment's .env "
                    "credential) to the path of nlmixr2_sse_10min.R")
    assert SESSION_COUNT >= 1, "NLMIXR_10MIN_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    install_missing_r_packages(browser_name, browser_type_launch_args, browser_context_args, USER, R_PACKAGES,
                               NLMIXR_10MIN_SESSION_PREFIX + "SETUP")
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILES, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=DELETE_OUTPUT, poll_interval_ms=POLL_INTERVAL_MS,
        summary_file=RUN_SUMMARY_FILE or None,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session (nlmixr2 script)", threshold=THRESHOLD_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
