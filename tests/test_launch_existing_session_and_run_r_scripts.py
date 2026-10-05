"""Open the sessions already created through the Workbench API
(AUTO_API_SESSION_105 .. AUTO_API_SESSION_109 by default; see
API_SESSION_START/API_SESSION_END), each in its own tab, and source the
high-throughput R script in every console concurrently, timing each run.

The sessions are not quit afterwards - they were created outside this test
and are reused - only the tabs are closed.
"""
import time

import pytest

from common.config import env
from common.launch_already_created_sessions import (
    api_session_names,
    close_launched_sessions,
    launch_already_created_sessions,
    run_script_in_launched_sessions,
)
from common.rstudio_workbenchjob import DEFAULT_WORKBENCH_JOB_SCRIPT
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_actions import login_to_posit_workbench

pytestmark = pytest.mark.rstudio_local

HTP_SCRIPT_PATH = env("HIGH_THROUGHPUT_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
# The script writes OUTPUT_FILE_NAME relative to its working directory, so
# each session setwd()s to OUTPUT_DIR/<session name> before sourcing it.
OUTPUT_DIR = env("HIGH_THROUGHPUT_JOB_OUTPUT_DIR", "/fsx/data/batch_mayur")
OUTPUT_FILE_NAME = "generated_data.csv"
SCRIPT_TIMEOUT_MS = int(env("HIGH_THROUGHPUT_JOB_TIMEOUT_MS", "1800000"))
# Set to "false" to keep each session's OUTPUT_FILE_NAME and folder.
DELETE_OUTPUT = env("HIGH_THROUGHPUT_JOB_DELETE_OUTPUT", "true").lower() != "false"
TIMINGS_LABEL = "existing_api_sessions"


def test_launch_existing_sessions_run_high_throughput_script_timed(context):
    """Open each existing API session in its own tab, setwd to
    OUTPUT_DIR/<session name>, source HTP_SCRIPT_PATH in every console
    concurrently and capture the time each run takes. Each run must create
    OUTPUT_FILE_NAME, which is then deleted (/fsx/data is close to full)
    unless HIGH_THROUGHPUT_JOB_DELETE_OUTPUT=false.
    Writes evidence/rstudio_script_timings_existing_api_sessions.csv.
    """
    session_names = api_session_names()
    home_url = login_to_posit_workbench(context.new_page())
    launched, runs = [], []

    started = time.time()
    try:
        launched = launch_already_created_sessions(context, home_url, session_names)
        not_opened = ["%s: %s" % (s.session_name, s.error) for s in launched if s.error]
        assert not not_opened, "sessions that could not be opened: %s" % not_opened

        runs = run_script_in_launched_sessions(
            launched, HTP_SCRIPT_PATH, working_dir=OUTPUT_DIR, timeout_ms=SCRIPT_TIMEOUT_MS,
            output_path=OUTPUT_FILE_NAME, per_session_dir=True, delete_output=DELETE_OUTPUT,
        )
        failures = ["%s: %s" % (r["session_name"], r["status"]) for r in runs if r["status"] != "ok"]
        print(
            "\n[rstudio-local] %s: %d of %d session(s) ran %s successfully, %.2fs in total"
            % (TIMINGS_LABEL, len(runs) - len(failures), len(runs), HTP_SCRIPT_PATH, time.time() - started)
        )
        assert not failures, "script runs that did not finish: %s" % failures
    finally:
        write_script_timings_csv(script_timings_csv_path(TIMINGS_LABEL), runs)
        close_launched_sessions(launched)
