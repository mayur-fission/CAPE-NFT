"""Sessions created through the Workbench API (POST /api/launch_session), one
per name in testdata/session_names_U<CSV_SESSIONS_USER>.csv, then driven in
the browser: an R script is sourced in every console concurrently and each
run is timed. The session ids are saved to testdata/session_ids_U<user>.csv.
The sessions are left running; only their tabs are closed.
"""
import os

import pytest

from common.api_helper import launch_sessions_from_csv, session_names_csv_path
from common.config import env
from common.script_run_scenarios import run_script_in_existing_sessions_scenario

pytestmark = pytest.mark.rstudio_local

CSV_SESSIONS_USER = int(env("CSV_SESSIONS_USER", "1"))
SESSION_NAMES_CSV = env("CSV_SESSIONS_NAMES_CSV", session_names_csv_path(CSV_SESSIONS_USER))
TIMINGS_LABEL = "new_api_sessions_U%d" % CSV_SESSIONS_USER
SCRIPT_PATH = env("CSV_SESSIONS_SCRIPT", "/home/posit/batch_mayur/generate_10kb_csv.R")
OUTPUT_DIR = env("CSV_SESSIONS_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data_10kb.csv"
SCRIPT_TIMEOUT_MS = int(env("HIGH_THROUGHPUT_JOB_TIMEOUT_MS", "1800000"))


def test_api_created_sessions_run_r_script_concurrently(context):
    """Every session created through the API opens in the browser and runs
    SCRIPT_PATH to completion, leaving OUTPUT_FILE_NAME in its own folder
    (kept). Writes evidence/rstudio_script_timings_new_api_sessions_U<user>.csv.

    Flow: create sessions via API -> login -> open each session in a tab ->
    setwd to OUTPUT_DIR/<session name> -> run script in all consoles at once
    -> check output files -> close tabs
    """
    assert os.path.exists(SESSION_NAMES_CSV), "session names CSV not found: %s" % SESSION_NAMES_CSV
    created, failed = launch_sessions_from_csv(SESSION_NAMES_CSV, user=CSV_SESSIONS_USER)
    assert not failed, "sessions that failed to launch: %s" % failed
    assert created, "no session names found in %s" % SESSION_NAMES_CSV

    not_opened, failures = run_script_in_existing_sessions_scenario(
        context, [s["session_name"] for s in created], TIMINGS_LABEL, SCRIPT_PATH,
        OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS, user=CSV_SESSIONS_USER,
    )
    assert not not_opened, "sessions that could not be opened: %s" % not_opened
    assert not failures, "script runs that did not finish: %s" % failures
