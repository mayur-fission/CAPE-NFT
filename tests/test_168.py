"""US-168, mrgsolve scenario 3: RSTUDIO_168_COUNT new RStudio Pro sessions,
each in its own tab, source the mrgsolve simulation script run_mrgsolve.R
(TEST_168_SCRIPT, the same script as test_167) at the same time and every
run is timed. The sessions are spread over TEST_168_BROWSERS browsers
(blank = one browser per 5 sessions), all logged in as one user.

The test does not limit how long the script runs: the run time is set in the
R script itself (TARGET_SECONDS), and each session simply waits until its
script stops on its own. No output file is checked.

Every run gets its own folder on the server,
<TEST_168_WORKING_DIR (blank = the folder TEST_168_SCRIPT is in)>/168_mrgsolve_scenario3_<datetime>/
holding one sub folder per session: session1, session2, ...
session<RSTUDIO_168_COUNT>. Session 168_AUTO_SESSION_<N> works in sessionN,
so everything its script writes stays there (never deleted).

Metrics, as in test_167 (one row per session):
evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv,
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json (first start to last
finish, min/avg/max), the session time charts
(common/session_time_report.py) and the run summary table read from each
session's mrgsolve_run_summary_PID_<run id>.csv,
evidence/mrgsolve_run_summary_<TIMINGS_LABEL>.csv (with an Average row over
the sessions). All are attached to Allure.

The R packages the script needs are not checked here: run
test_mrgsolve_setup.py first (once per user/server) to install them.
"""
import posixpath
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.mrgsolve_summary import read_mrgsolve_summary, report_run_summaries
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_naming_logic import TEST_168_NAME_PREFIX, concurrent_session_names
from common.session_time_report import describe_run, generate_session_time_report
from config.config import env

pytestmark = pytest.mark.rstudio_local

USER = int(env("TEST_168_USER", "1"))
SESSION_COUNT = int(env("RSTUDIO_168_COUNT", "3"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("TEST_168_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=TEST_168_NAME_PREFIX)
# 168_AUTO_SESSION_<N> works in session<N>
SESSION_FOLDERS = {name: "session%d" % (i + 1) for i, name in enumerate(SESSION_NAMES)}
# Seconds between starting one browser and the next, so their logins don't clash
BROWSER_STAGGER_S = int(env("TEST_168_BROWSER_STAGGER_S", "30"))
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATH = env("TEST_168_SCRIPT")
# Folder the run folder goes in (blank = the folder TEST_168_SCRIPT is in)
WORKING_DIR = env("TEST_168_WORKING_DIR") or (posixpath.dirname(SCRIPT_PATH) if SCRIPT_PATH else None)
RUN_STARTED = time.strftime("%Y%m%d-%H%M%S")
# This run's own folder on the server, holding session1 .. session<N>
RUN_FOLDER = "168_mrgsolve_scenario3_%s" % RUN_STARTED
RUN_DIR = posixpath.join(WORKING_DIR, RUN_FOLDER) if WORKING_DIR else None
LAUNCH_ATTEMPTS = int(env("TEST_168_LAUNCH_ATTEMPTS", "3"))
# Longest one session launch attempt may take, in minutes
LAUNCH_TIMEOUT_MIN = float(env("TEST_168_LAUNCH_TIMEOUT_MIN", "3"))
LAUNCH_RETRY_WAIT_S = int(env("TEST_168_LAUNCH_RETRY_WAIT_S", "15"))
# How often each console is checked for the end of the script
POLL_INTERVAL_MS = int(env("TEST_168_POLL_MS", "500"))
# Slow-session limit drawn on the session time charts (blank = none)
THRESHOLD_S = float(env("TEST_168_THRESHOLD_S") or 0) or None
# The test never stops the script: the R script sets its own run time and
# stops by itself. The shared runner needs a number, so it gets one far
# beyond any real run (7 days, in minutes) - it is not a test duration.
NO_LIMIT_MIN = 7 * 24 * 60
# Same name as the run folder: this run's CSV, charts and summary never
# overwrite another run's
TIMINGS_LABEL = RUN_FOLDER


def test_mrgsolve_concurrent_sessions_168(browser_name, browser_type_launch_args, browser_context_args):
    """The mrgsolve R script runs in RSTUDIO_168_COUNT new sessions at the
    same time, each until it stops by itself, each in its own sessionN
    folder of this run's 168_mrgsolve_scenario3_<datetime> folder; every
    session's timing and run summary are reported.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> create each session's folder, setwd there
    -> wait for all browsers -> source the script in all tabs at once ->
    wait until all finish -> read each run summary -> close tabs -> quit
    the sessions. Then: timings CSV, charts and run summary table.
    """
    if not SCRIPT_PATH:
        pytest.skip("TEST_168_SCRIPT is not set - set it in .env (on Jenkins: in the environment's .env "
                    "credential) to the path of run_mrgsolve.R")
    assert SESSION_COUNT >= 1, "RSTUDIO_168_COUNT must be at least 1, got %d" % SESSION_COUNT
    print("\n[rstudio-local] test_168 run folder: %s" % RUN_DIR)
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, RUN_DIR, None, int(NO_LIMIT_MIN * 60000),
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS,
        launch_timeout_ms=int(LAUNCH_TIMEOUT_MIN * 60000), retry_wait_s=LAUNCH_RETRY_WAIT_S,
        delete_output=False, poll_interval_ms=POLL_INTERVAL_MS, session_folders=SESSION_FOLDERS,
        after_run=read_mrgsolve_summary,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    report_run_summaries(runs, TIMINGS_LABEL, describe_run(SESSION_COUNT, BROWSER_COUNT, USER), RUN_DIR)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session - test_168 mrgsolve scenario 3",
        threshold=THRESHOLD_S, browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
