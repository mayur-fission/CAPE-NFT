"""Start a long-running R script in RSTUDIO_SESSION_COUNT new sessions (each in
its own tab), close every tab straight away and check the sessions survive
RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS on their own. Every session
created is force-quit afterwards.

The script (default /home/posit/sample_run_sleep.R) must already exist on the
server.
"""
import pytest

from common.config import env
from common.rstudio_workbenchjob import DEFAULT_WORKBENCH_JOB_SCRIPT
from common.script_run_scenarios import (
    source_script_close_tabs_then_quit_scenario,
    start_jobs_close_tabs_then_stop_jobs_scenario,
)

pytestmark = pytest.mark.rstudio_local

SCRIPT_PATH = env("RSTUDIO_WORKBENCH_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", required=True))
TEST_DURATION_SECONDS = float(env("RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS", "300"))


@pytest.mark.skip()
def test_workbench_jobs_keep_running_after_tabs_closed(context):
    """A Workbench job started in each session is still running
    TEST_DURATION_SECONDS after its tab was closed.

    Flow: login -> per session: launch new session in a tab -> start
    SCRIPT_PATH as a Workbench job -> close tab -> wait -> reopen each
    session -> stop its running job -> quit sessions
    """
    failures = start_jobs_close_tabs_then_stop_jobs_scenario(
        context, SESSION_COUNT, SCRIPT_PATH, TEST_DURATION_SECONDS
    )
    assert not failures, "reopen/stop failed after %.0fs: %s" % (TEST_DURATION_SECONDS, failures)


def test_console_scripts_run_after_tabs_closed_then_quit(context):
    """Sessions running a console script (the default loops forever) can be
    left without a tab for TEST_DURATION_SECONDS and then quit cleanly.

    Flow: login -> per session: launch new session in a tab -> source
    SCRIPT_PATH in the console -> close tab -> wait -> quit sessions
    """
    source_script_close_tabs_then_quit_scenario(
        context, SESSION_COUNT, SCRIPT_PATH, TEST_DURATION_SECONDS
    )
