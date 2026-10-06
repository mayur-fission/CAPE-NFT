"""High-throughput R script (HIGH_THROUGHPUT_JOB_SCRIPT) in new RStudio Pro
sessions, each in its own tab: either started and left running after the
tab closes, or sourced in the console and timed to completion. Every session
created is force-quit afterwards.

The script writes OUTPUT_FILE_NAME relative to its working directory, so each
session setwd()s to OUTPUT_DIR (OUTPUT_DIR/<session name> with several
sessions) first.
"""
import pytest

from common.config import env
from common.rstudio_workbenchjob import DEFAULT_WORKBENCH_JOB_SCRIPT
from common.script_run_scenarios import (
    run_script_in_new_sessions_scenario,
    source_script_close_tabs_then_quit_scenario,
    start_jobs_close_tabs_then_stop_jobs_scenario,
)

pytestmark = pytest.mark.rstudio_local

HTP_SCRIPT_PATH = env("HIGH_THROUGHPUT_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", required=True))
TEST_DURATION_SECONDS = float(env("RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS", "300"))
OUTPUT_DIR = env("HIGH_THROUGHPUT_JOB_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data.csv"
SCRIPT_TIMEOUT_MS = int(env("HIGH_THROUGHPUT_JOB_TIMEOUT_MS", "1800000"))


@pytest.mark.skip()
def test_high_throughput_jobs_keep_running_after_tabs_closed(context):
    """The script started as a Workbench job in each session is still
    running TEST_DURATION_SECONDS after its tab was closed.

    Flow: login -> per session: launch new session in a tab -> start
    script as a Workbench job -> close tab -> wait -> reopen each session ->
    stop its running job -> quit sessions
    """
    failures = start_jobs_close_tabs_then_stop_jobs_scenario(
        context, SESSION_COUNT, HTP_SCRIPT_PATH, TEST_DURATION_SECONDS
    )
    assert not failures, "reopen/stop failed after %.0fs: %s" % (TEST_DURATION_SECONDS, failures)


def test_high_throughput_console_scripts_run_after_tabs_closed_then_quit(context):
    """Sessions running the script in the console can be left without a tab
    for TEST_DURATION_SECONDS and then quit cleanly, which ends the script.

    Flow: login -> per session: launch new session in a tab -> source
    script in the console -> close tab -> wait -> quit sessions
    """
    source_script_close_tabs_then_quit_scenario(
        context, SESSION_COUNT, HTP_SCRIPT_PATH, TEST_DURATION_SECONDS
    )


@pytest.mark.skip()
def test_single_session_high_throughput_script_time(context):
    """One session runs the script to completion and creates
    OUTPUT_FILE_NAME (deleted afterwards).
    Writes evidence/rstudio_script_timings_high_throughput_single_session.csv.

    Flow: login -> launch new session -> setwd to OUTPUT_DIR -> run script
    -> check and delete output file -> quit session
    """
    failures = run_script_in_new_sessions_scenario(
        context, 1, "high_throughput_single_session", HTP_SCRIPT_PATH,
        OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS,
    )
    assert not failures, "script runs that did not finish: %s" % failures


@pytest.mark.skip()
def test_concurrent_sessions_high_throughput_script_time(context):
    """SESSION_COUNT sessions run the script at once, each creating
    OUTPUT_FILE_NAME in its own folder (deleted afterwards).
    Writes evidence/rstudio_script_timings_high_throughput_multiple_sessions.csv.

    Flow: login -> launch new sessions, one per tab -> setwd to
    OUTPUT_DIR/<session name> -> run script in all consoles at once -> check
    and delete output files -> quit sessions
    """
    failures = run_script_in_new_sessions_scenario(
        context, SESSION_COUNT, "high_throughput_multiple_sessions", HTP_SCRIPT_PATH,
        OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS, per_session_dir=True,
    )
    assert not failures, "script runs that did not finish: %s" % failures
