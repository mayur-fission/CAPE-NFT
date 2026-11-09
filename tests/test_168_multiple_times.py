"""US-168, mrgsolve scenario 2: run_mrgsolve.R (TEST_168_SCRIPT, the same
script as test_167 and test_168) in one new RStudio Pro session, timed, done
TEST_168_MULTIPLE_RUNS times in a row, waiting TEST_168_MULTIPLE_WAIT_MIN
minutes after each run finishes before the next one starts.

The test does not limit how long the script runs: the run time is set in the
R script itself (TARGET_SECONDS), and each run simply waits until the script
stops on its own. No output file is checked.

Every run of the test gets its own folder on the server,
<TEST_168_WORKING_DIR (blank = the folder TEST_168_SCRIPT is in)>/168_mrgsolve_scenario2_<datetime>/
holding one sub folder per run: run1, run2, ... run<TEST_168_MULTIPLE_RUNS>.
Run N's session works in runN, so everything the script writes in that run
stays there (never deleted).

Each run logs in with a fresh browser, launches session 168M_AUTO_SESSION_1,
runs the script and quits the session. A failed run does not stop the later
ones - the test fails at the end if any run failed.

Metrics, as in test_167 / test_168 but one row per run (run_1, run_2, ...) instead of
per session: evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv,
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, the time charts
(common/session_time_report.py) and the run summary table read from each
run's mrgsolve_run_summary_PID_<run id>.csv,
evidence/mrgsolve_run_summary_<TIMINGS_LABEL>.csv (with an Average row over
the runs). All are attached to Allure.

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
from common.session_naming_logic import TEST_168_MULTIPLE_TIMES_NAME_PREFIX, concurrent_session_names
from common.session_time_report import generate_session_time_report
from common.step_log import log_step
from config.config import env

pytestmark = pytest.mark.rstudio_local

BROWSER_COUNT = 1
USER = int(env("TEST_168_USER", "1"))
SESSION_NAMES = concurrent_session_names(1, prefix=TEST_168_MULTIPLE_TIMES_NAME_PREFIX)
# How many times the run is done, one after another
RUNS = int(env("TEST_168_MULTIPLE_RUNS", "3"))
# Minutes to wait after a run finishes before the next one starts (decimals allowed)
WAIT_BETWEEN_RUNS_MIN = float(env("TEST_168_MULTIPLE_WAIT_MIN") or 0)
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATH = env("TEST_168_SCRIPT")
# Folder the test's folder goes in (blank = the folder TEST_168_SCRIPT is in)
WORKING_DIR = env("TEST_168_WORKING_DIR") or (posixpath.dirname(SCRIPT_PATH) if SCRIPT_PATH else None)
RUN_STARTED = time.strftime("%Y%m%d-%H%M%S")
# This test's own folder on the server, holding run1 .. run<RUNS>
TEST_FOLDER = "168_mrgsolve_scenario2_%s" % RUN_STARTED
TEST_DIR = posixpath.join(WORKING_DIR, TEST_FOLDER) if WORKING_DIR else None
LAUNCH_ATTEMPTS = int(env("TEST_168_LAUNCH_ATTEMPTS", "3"))
# Longest one session launch attempt may take, in minutes
LAUNCH_TIMEOUT_MIN = float(env("TEST_168_LAUNCH_TIMEOUT_MIN", "3"))
LAUNCH_RETRY_WAIT_S = int(env("TEST_168_LAUNCH_RETRY_WAIT_S", "15"))
# How often the console is checked for the end of the script
POLL_INTERVAL_MS = int(env("TEST_168_POLL_MS", "500"))
# Slow-run limit drawn on the time charts (blank = none)
THRESHOLD_S = float(env("TEST_168_THRESHOLD_S") or 0) or None
# The test never stops the script: the R script sets its own run time and
# stops by itself. The shared runner needs a number, so it gets one far
# beyond any real run (7 days, in minutes) - it is not a test duration.
NO_LIMIT_MIN = 7 * 24 * 60
# Same name as the test folder: this test's CSV, charts and summary never
# overwrite another's
TIMINGS_LABEL = TEST_FOLDER


def _describe():
    return "%d run%s of 1 session, %g min apart, user %d" % (
        RUNS, "" if RUNS == 1 else "s", WAIT_BETWEEN_RUNS_MIN, USER)


def test_mrgsolve_single_session_168_multiple_times(browser_name, browser_type_launch_args,
                                                    browser_context_args):
    """The mrgsolve R script runs RUNS times, one run after another, each in
    a new session and in its own runN folder of this test's
    168_mrgsolve_scenario2_<datetime> folder; the timing and run summary of
    every run are reported.

    Flow, RUNS times: (wait WAIT_BETWEEN_RUNS_MIN after the previous run,
    except before the first) -> login -> quit a leftover session with this
    name -> launch the session -> create runN, setwd there -> source the
    script -> wait until it finishes -> read its run summary -> close tab ->
    quit the session. Then: timings CSV, charts and run summary table over
    all runs.
    """
    if not SCRIPT_PATH:
        pytest.skip("TEST_168_SCRIPT is not set - set it in .env (on Jenkins: in the environment's .env "
                    "credential) to the path of run_mrgsolve.R")
    assert RUNS >= 1, "TEST_168_MULTIPLE_RUNS must be at least 1, got %s" % RUNS
    assert WAIT_BETWEEN_RUNS_MIN >= 0, "TEST_168_MULTIPLE_WAIT_MIN must be 0 or more, got %s" % WAIT_BETWEEN_RUNS_MIN
    print("\n[rstudio-local] test_168_multiple_times folder: %s (%s)" % (TEST_DIR, _describe()))
    all_runs, failed = [], {}
    for run in range(1, RUNS + 1):
        if run > 1 and WAIT_BETWEEN_RUNS_MIN > 0:
            log_step("Run %d of %d finished - waiting %g min before run %d", run - 1, RUNS,
                     WAIT_BETWEEN_RUNS_MIN, run)
            time.sleep(WAIT_BETWEEN_RUNS_MIN * 60)
        run_folder = "run%d" % run
        log_step("Run %d of %d: output in %s", run, RUNS, posixpath.join(TEST_DIR, run_folder))
        try:
            runs, errors = run_script_in_concurrent_sessions(
                browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES,
                BROWSER_COUNT, "%s_%s" % (TIMINGS_LABEL, run_folder), SCRIPT_PATH, TEST_DIR, None,
                int(NO_LIMIT_MIN * 60000),
                attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=int(LAUNCH_TIMEOUT_MIN * 60000),
                retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=False, poll_interval_ms=POLL_INTERVAL_MS,
                session_folders={name: run_folder for name in SESSION_NAMES},
                after_run=read_mrgsolve_summary,
            )
        except Exception as exc:
            runs, errors = [], ["error: %s" % exc]
        if not runs:
            runs = [{"session_name": SESSION_NAMES[0], "started": None, "ended": None,
                     "status": "; ".join(errors) or "not run"}]
        # One row per run in the reports: run_1, run_2, ... (the session
        # itself is 168M_AUTO_SESSION_1 every time)
        for record in runs:
            record["session_name"] = "run_%d" % run
        all_runs += runs
        if errors:
            log_step("Run %d of %d failed: %s", run, RUNS, "; ".join(str(e) for e in errors))
            failed[run] = errors
        else:
            log_step("Run %d of %d finished ok", run, RUNS)
    print("\n[rstudio-local] %d/%d runs passed" % (RUNS - len(failed), RUNS))

    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, all_runs)
    summarize_concurrent_runs(all_runs, TIMINGS_LABEL, RUNS, BROWSER_COUNT)
    report_run_summaries(all_runs, TIMINGS_LABEL, _describe(), TEST_DIR)
    generate_session_time_report(
        timings_csv, title="Time taken per run - test_168_multiple_times mrgsolve scenario 2 (single session, repeated)",
        threshold=THRESHOLD_S, browser=browser_name, test_name=_describe(), script=SCRIPT_PATH,
    )
    assert not failed, "%d of %d runs failed: %s" % (len(failed), RUNS, failed)
