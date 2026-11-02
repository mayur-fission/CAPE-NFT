"""Like test_concurrent_sessions_10min_s.py, but sources the mrgsolve
simulation script run_mrgsolve.R (MRGSOLVE_SCRIPT). MRGSOLVE_SESSIONS
sessions (default 1) launched from the UI, named MRGSOLVE_SESSION_1 ..
_<N>, spread over MRGSOLVE_SESSIONS_BROWSERS browsers (blank = one
browser per 5 sessions), all logged in as one user. Once every browser has
launched its sessions, SCRIPT_PATH is sourced in all of them at the same time
and each run is timed (timeout MRGSOLVE_SCRIPT_TIMEOUT_MIN minutes, default 30).

The R packages the script needs are not checked here: run
test_mrgsolve_setup.py first (once per user/server) to install them.

Every run gets its own folder, <working dir> (MRGSOLVE_WORKING_DIR, else the
folder MRGSOLVE_SCRIPT is in)/MRGSOLVE_USER_<session count>_<datetime>/
(e.g. MRGSOLVE_USER_5_20261029-203754), and in it each session runs the script in its own
folder: MRGSOLVE_SESSION_<N> in MRGSOLVE_OUTPUT_SESSION_<N>/.
Set MRGSOLVE_OUTPUT_FILES (comma-separated, relative to the session
folder) to the files the script writes, and a run only counts as ok if all of
them are there afterwards (never deleted: the data stays on the server after the
test); unset, only the script finishing without
an error is checked. MRGSOLVE_RUN_SUMMARY names a run summary file in the
pmx scripts' format (replicates, runtime, stop reason) to read into the
results (unset = none).

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per session (common/session_time_report.py) next to the CSV, all
attached to Allure along with the screenshots. Set
MRGSOLVE_THRESHOLD_S to mark a slow-session limit on the charts.

Each finished session's mrgsolve_run_summary_PID_<run id>.csv (written by
the script in its session folder) is read into a run summary table, one row
per session: evidence/mrgsolve_run_summary_<TIMINGS_LABEL>.csv, printed and
attached to Allure as a table.
"""
import posixpath
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.mrgsolve_summary import read_mrgsolve_summary, report_run_summaries
from common.result_printing import summarize_concurrent_runs
from common.parallel_browsers import browsers_for
from config.config import env, env_list
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report
from common.session_naming_logic import MRGSOLVE_SESSION_PREFIX, concurrent_session_names

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("MRGSOLVE_SESSIONS", "1"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("MRGSOLVE_SESSIONS_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=MRGSOLVE_SESSION_PREFIX)
# MRGSOLVE_SESSION_<N> writes its output to MRGSOLVE_OUTPUT_SESSION_<N>
SESSION_FOLDERS = {name: "MRGSOLVE_OUTPUT_SESSION_%d" % (i + 1) for i, name in enumerate(SESSION_NAMES)}
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATH = env("MRGSOLVE_SCRIPT")
# Folder the run folders go in (blank = the folder MRGSOLVE_SCRIPT is in)
WORKING_DIR = env("MRGSOLVE_WORKING_DIR") or (posixpath.dirname(SCRIPT_PATH) if SCRIPT_PATH else None)
RUN_STARTED = time.strftime("%Y%m%d-%H%M%S")
# This run's own folder, holding the session folders
RUN_DIR = posixpath.join(WORKING_DIR, "MRGSOLVE_USER_%d_%s" % (SESSION_COUNT, RUN_STARTED)) if WORKING_DIR else None
# Files the script writes under its session folder; all must exist for a run
# to count as ok. Unset = no output check.
OUTPUT_FILES = env_list("MRGSOLVE_OUTPUT_FILES")
RUN_SUMMARY_FILE = env("MRGSOLVE_RUN_SUMMARY", "")
# Longest one script run may take, in minutes (the script runs ~20 min; headroom on top)
SCRIPT_TIMEOUT_MIN = float(env("MRGSOLVE_SCRIPT_TIMEOUT_MIN") or 30)
SCRIPT_TIMEOUT_MS = int(SCRIPT_TIMEOUT_MIN * 60000)
# How often each console is checked for the end of the script.
POLL_INTERVAL_MS = int(env("MRGSOLVE_POLL_MS", "500"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "mrgsolve_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, RUN_STARTED)
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("MRGSOLVE_THRESHOLD_S") or 0) or None

def test_mrgsolve_r_script(browser_name, browser_type_launch_args, browser_context_args):
    """The mrgsolve R script finishes ok (with its output files, when
    MRGSOLVE_OUTPUT_FILES is set) in every session, each session's
    output in its MRGSOLVE_OUTPUT_SESSION_<N> folder of this run's folder.

    (R packages: test_mrgsolve_setup.py.)

    Flow: per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all browsers -> source the script
    in all tabs at once, each in its own folder -> wait for all -> check each
    session's output files (reading its run summary) -> close tabs -> quit
    created sessions -> run summary table
    """
    if not SCRIPT_PATH:
        pytest.skip("MRGSOLVE_SCRIPT is not set - set it in .env (on Jenkins: in the environment's .env "
                    "credential) to the path of run_mrgsolve.R")
    assert SESSION_COUNT >= 1, "MRGSOLVE_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, RUN_DIR, OUTPUT_FILES, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=False, poll_interval_ms=POLL_INTERVAL_MS,
        summary_file=RUN_SUMMARY_FILE or None, session_folders=SESSION_FOLDERS,
        after_run=read_mrgsolve_summary,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    report_run_summaries(runs, TIMINGS_LABEL, describe_run(SESSION_COUNT, BROWSER_COUNT, USER), RUN_DIR)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session (mrgsolve script)", threshold=THRESHOLD_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
