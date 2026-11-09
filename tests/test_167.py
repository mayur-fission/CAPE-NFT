"""US-167, mrgsolve scenario 1: one new RStudio Pro session sources the
mrgsolve simulation script run_mrgsolve.R (TEST_167_SCRIPT) in its console
and the run is timed.

The test does not limit how long the script runs: the run time is set in the
R script itself (TARGET_SECONDS), and the test simply waits until the script
stops on its own. No output file is checked.

Every run gets its own folder on the server,
<TEST_167_WORKING_DIR (blank = the folder TEST_167_SCRIPT is in)>/167_mrgsolve_scenario1_<datetime>/
(e.g. 167_mrgsolve_scenario1_20261109-143005), which is the session's working
directory: everything the script writes stays there (never deleted).

Metrics, as in test_mrgsolve_concurrent.py: the session's timing goes to
evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv and the overall summary
to evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with the session
time charts (common/session_time_report.py); the script's
mrgsolve_run_summary_PID_<run id>.csv is read into
evidence/mrgsolve_run_summary_<TIMINGS_LABEL>.csv. All are attached to Allure.

The R packages the script needs are not checked here: run
test_mrgsolve_setup.py first (once per user/server) to install them.
"""
import posixpath
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.mrgsolve_summary import read_mrgsolve_summary, report_run_summaries
from common.result_printing import summarize_concurrent_runs
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_naming_logic import TEST_167_NAME_PREFIX, concurrent_session_names
from common.session_time_report import describe_run, generate_session_time_report
from config.config import env

pytestmark = pytest.mark.rstudio_local

SESSION_COUNT = 1
BROWSER_COUNT = 1
USER = int(env("TEST_167_USER", "1"))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=TEST_167_NAME_PREFIX)
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATH = env("TEST_167_SCRIPT")
# Folder the run folder goes in (blank = the folder TEST_167_SCRIPT is in)
WORKING_DIR = env("TEST_167_WORKING_DIR") or (posixpath.dirname(SCRIPT_PATH) if SCRIPT_PATH else None)
RUN_STARTED = time.strftime("%Y%m%d-%H%M%S")
# This run's own folder on the server - the session's working directory
RUN_FOLDER = "167_mrgsolve_scenario1_%s" % RUN_STARTED
RUN_DIR = posixpath.join(WORKING_DIR, RUN_FOLDER) if WORKING_DIR else None
LAUNCH_ATTEMPTS = int(env("TEST_167_LAUNCH_ATTEMPTS", "3"))
# Longest one session launch attempt may take, in minutes
LAUNCH_TIMEOUT_MIN = float(env("TEST_167_LAUNCH_TIMEOUT_MIN", "3"))
LAUNCH_RETRY_WAIT_S = int(env("TEST_167_LAUNCH_RETRY_WAIT_S", "15"))
# How often the console is checked for the end of the script
POLL_INTERVAL_MS = int(env("TEST_167_POLL_MS", "500"))
# Slow-session limit drawn on the session time chart (blank = none)
THRESHOLD_S = float(env("TEST_167_THRESHOLD_S") or 0) or None
# The test never stops the script: the R script sets its own run time and
# stops by itself. The shared runner needs a number, so it gets one far
# beyond any real run (7 days, in minutes) - it is not a test duration.
NO_LIMIT_MIN = 7 * 24 * 60
# Same name as the run folder: this run's CSV, charts and summary never
# overwrite another run's
TIMINGS_LABEL = RUN_FOLDER


def test_mrgsolve_single_session_167(browser_name, browser_type_launch_args, browser_context_args):
    """The mrgsolve R script runs in one new session until it stops by
    itself, in this run's 167_mrgsolve_scenario1_<datetime> folder; its
    timing and run summary are reported.

    Flow: login -> quit a leftover session with this name -> launch the
    session -> create the run folder, setwd there -> source the script ->
    wait until it finishes -> read its run summary -> close tab -> quit the
    session -> timings CSV, charts and run summary table
    """
    if not SCRIPT_PATH:
        pytest.skip("TEST_167_SCRIPT is not set - set it in .env (on Jenkins: in the environment's .env "
                    "credential) to the path of run_mrgsolve.R")
    print("\n[rstudio-local] test_167 run folder: %s" % RUN_DIR)
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, WORKING_DIR, None, int(NO_LIMIT_MIN * 60000),
        attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=int(LAUNCH_TIMEOUT_MIN * 60000),
        retry_wait_s=LAUNCH_RETRY_WAIT_S,
        delete_output=False, poll_interval_ms=POLL_INTERVAL_MS,
        session_folders={name: RUN_FOLDER for name in SESSION_NAMES},
        after_run=read_mrgsolve_summary,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    report_run_summaries(runs, TIMINGS_LABEL, describe_run(SESSION_COUNT, BROWSER_COUNT, USER), RUN_DIR)
    generate_session_time_report(
        timings_csv, title="Time taken - test_167 mrgsolve scenario 1 (single session)", threshold=THRESHOLD_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
