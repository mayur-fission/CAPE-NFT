"""Launch RSTUDIO_SESSION_COUNT new RStudio Pro sessions, each in its own
browser tab. In each one, start an R script as a Workbench job and close the
tab as soon as the job has started. After
RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS, reopen every session, open
Workbench Jobs and click Stop on a running job, then force-quit every
session created.

test_create_sessions_run_r_in_console_close_tab does the same, but sources
the script in each session's console instead, closing the tab as soon as it
has been submitted.

The script (default /fsx/data/sample_run_sleep.R) must already exist on the
server.
"""
import posixpath
import time

import pytest

from common.config import env
from common.rstudio_session_helper import row_ids
from common.rstudio_workbenchjob import (
    DEFAULT_WORKBENCH_JOB_SCRIPT,
    launch_sessions_and_source_script,
    launch_sessions_and_start_jobs,
    reopen_sessions_and_stop_jobs,
)
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions

pytestmark = pytest.mark.rstudio_local

SCRIPT_PATH = env("RSTUDIO_WORKBENCH_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
JOB_NAME = posixpath.basename(SCRIPT_PATH)
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", required=True))
TEST_DURATION_SECONDS = float(env("RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS", "300"))

@pytest.mark.skip()
def test_create_sessions_run_workbench_job_close_tab(context):
    home_page = context.new_page()
    home_url = login_to_posit_workbench(home_page)
    before_ids = row_ids(home_page)

    try:
        session_names = launch_sessions_and_start_jobs(context, home_url, SESSION_COUNT, SCRIPT_PATH)

        print("\n[rstudio-local] all tabs closed, waiting %.0fs before reopening sessions" % TEST_DURATION_SECONDS)
        home_page.wait_for_timeout(TEST_DURATION_SECONDS * 1000)

        failures = reopen_sessions_and_stop_jobs(home_page, home_url, session_names, JOB_NAME)
        assert not failures, "reopen/stop failed after %.0fs: %s" % (TEST_DURATION_SECONDS, failures)
    finally:
        cleanup_and_verify_sessions(home_page, home_url, before_ids)


def test_create_sessions_run_r_in_console_close_tab(context):
    """Launch RSTUDIO_SESSION_COUNT sessions, each in its own tab, source the
    script in the console and close the tab without waiting for it (the
    default script loops forever). After
    RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS, quit every session created,
    which ends the script.
    """
    home_page = context.new_page()
    home_url = login_to_posit_workbench(home_page)
    before_ids = row_ids(home_page)

    try:
        launch_sessions_and_source_script(context, home_url, SESSION_COUNT, SCRIPT_PATH)

        print("\n[rstudio-local] all tabs closed, waiting %.0fs before quitting sessions" % TEST_DURATION_SECONDS)
        home_page.wait_for_timeout(TEST_DURATION_SECONDS * 1000)

        started = time.time()
        quit_ids = cleanup_and_verify_sessions(home_page, home_url, before_ids)
        print("\n[rstudio-local] quit %d session(s) in %.2fs" % (len(quit_ids), time.time() - started))
    finally:
        # Only does anything if the test failed before the quit above.
        cleanup_and_verify_sessions(home_page, home_url, before_ids)
