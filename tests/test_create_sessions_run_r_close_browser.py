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
from common import locators
from common.rstudio_console_commands import r_string
from common.rstudio_session_helper import row_ids
from common.rstudio_workbenchjob import (
    DEFAULT_WORKBENCH_JOB_SCRIPT,
    get_job_status,
    start_workbench_job,
    stop_workbench_job,
)
from common.session_actions import (
    launch_session,
    login_to_posit_workbench,
    open_existing_session,
    submit_console_command,
)
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
        session_names = []
        for _ in range(SESSION_COUNT):
            tab = context.new_page()
            try:
                launch = launch_session(tab, home_url)
                session_names.append(launch.session_name)
                job_name, submit_elapsed = start_workbench_job(tab, SCRIPT_PATH)
                print(
                    "\n[rstudio-local] session %s launched in %.2fs, job %s started in %.2fs (status: %s)"
                    % (launch.session_name, launch.elapsed_s, job_name, submit_elapsed,
                       get_job_status(tab, job_name))
                )
            finally:
                tab.close()

        print("\n[rstudio-local] all tabs closed, waiting %.0fs before reopening sessions" % TEST_DURATION_SECONDS)
        home_page.wait_for_timeout(TEST_DURATION_SECONDS * 1000)

        failures = []
        for session_name in session_names:
            try:
                reopen = open_existing_session(home_page, home_url, session_name)
                stopped = stop_workbench_job(home_page, JOB_NAME)
                print(
                    "\n[rstudio-local] session %s reopened in %.2fs, %s job %s (status: %s)"
                    % (session_name, reopen.elapsed_s, JOB_NAME,
                       "stopped" if stopped else "was not running", get_job_status(home_page, JOB_NAME))
                )
                if not stopped:
                    failures.append("%s: no running %s job to stop" % (session_name, JOB_NAME))
            except Exception as exc:
                failures.append("%s: %s" % (session_name, exc))
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
    source_command = "source(%s)" % r_string(SCRIPT_PATH)

    try:
        for _ in range(SESSION_COUNT):
            tab = context.new_page()
            try:
                launch = launch_session(tab, home_url)
                submit_console_command(tab, source_command)
                # The console echoes the command once R has accepted it.
                tab.locator(locators.CONSOLE_OUTPUT_SELECTOR).get_by_text(source_command).first.wait_for(
                    state="visible", timeout=10000
                )
                print(
                    "\n[rstudio-local] session %s launched in %.2fs, %s submitted in the console"
                    % (launch.session_name, launch.elapsed_s, source_command)
                )
            finally:
                tab.close()

        print("\n[rstudio-local] all tabs closed, waiting %.0fs before quitting sessions" % TEST_DURATION_SECONDS)
        home_page.wait_for_timeout(TEST_DURATION_SECONDS * 1000)

        started = time.time()
        quit_ids = cleanup_and_verify_sessions(home_page, home_url, before_ids)
        print("\n[rstudio-local] quit %d session(s) in %.2fs" % (len(quit_ids), time.time() - started))
    finally:
        # Only does anything if the test failed before the quit above.
        cleanup_and_verify_sessions(home_page, home_url, before_ids)
