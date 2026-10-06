"""Reuse RStudio sessions that already exist on Workbench: open each in its own
tab and source an R script in every console concurrently, timing each run.
Nothing is created; the sessions are left running and only their tabs are
closed.
"""
import pytest

from common.api_helper import read_session_names, session_ids_csv_path
from common.config import env
from common.launch_already_created_sessions import api_session_names
from common.rstudio_workbenchjob import DEFAULT_WORKBENCH_JOB_SCRIPT
from common.script_run_scenarios import run_script_in_existing_sessions_scenario

pytestmark = pytest.mark.rstudio_local

SCRIPT_TIMEOUT_MS = int(env("HIGH_THROUGHPUT_JOB_TIMEOUT_MS", "1800000"))

# Named API sessions (AUTO_API_SESSION_<API_SESSION_START..API_SESSION_END>)
HTP_SCRIPT_PATH = env("HIGH_THROUGHPUT_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
OUTPUT_DIR = env("HIGH_THROUGHPUT_JOB_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data.csv"
# Set to "false" to keep each session's OUTPUT_FILE_NAME and folder.
DELETE_OUTPUT = env("HIGH_THROUGHPUT_JOB_DELETE_OUTPUT", "true").lower() != "false"
TIMINGS_LABEL = "existing_api_sessions"

# Sessions recorded in testdata/session_ids_U<CSV_SESSIONS_USER>.csv
CSV_SESSIONS_USER = int(env("CSV_SESSIONS_USER", "1"))
CSV_SCRIPT_PATH = env("CSV_SESSIONS_SCRIPT", "/home/posit/generate_10kb_csv.R")
CSV_OUTPUT_DIR = env("CSV_SESSIONS_OUTPUT_DIR", "/home/posit")
CSV_OUTPUT_FILE_NAME = "generated_data_10kb.csv"
CSV_TIMINGS_LABEL = "csv_api_sessions_U%d" % CSV_SESSIONS_USER


def test_existing_api_sessions_run_high_throughput_script_timed(context):
    """Sessions AUTO_API_SESSION_<API_SESSION_START..API_SESSION_END> each run
    the high-throughput script to completion and create OUTPUT_FILE_NAME
    (deleted afterwards unless HIGH_THROUGHPUT_JOB_DELETE_OUTPUT=false).
    Writes evidence/rstudio_script_timings_existing_api_sessions.csv.

    Flow: login -> open each existing session in a tab -> setwd to
    OUTPUT_DIR/<session name> -> run script in all consoles at once ->
    check (and delete) output files -> close tabs
    """
    not_opened, failures = run_script_in_existing_sessions_scenario(
        context, api_session_names(), TIMINGS_LABEL, HTP_SCRIPT_PATH,
        OUTPUT_DIR, OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS, delete_output=DELETE_OUTPUT,
    )
    assert not not_opened, "sessions that could not be opened: %s" % not_opened
    assert not failures, "script runs that did not finish: %s" % failures


def test_sessions_from_ids_csv_run_r_script_timed(context):
    """The sessions recorded in testdata/session_ids_U<user>.csv (written when
    they were created through the API) each run CSV_SCRIPT_PATH to completion
    and leave CSV_OUTPUT_FILE_NAME in their folder (kept).
    Writes evidence/rstudio_script_timings_csv_api_sessions_U<user>.csv.

    Flow: read session names from CSV -> login -> open each session in a tab
    -> setwd to CSV_OUTPUT_DIR/<session name> -> run script in all consoles
    at once -> check output files -> close tabs
    """
    csv_path = session_ids_csv_path(CSV_SESSIONS_USER)
    session_names = read_session_names(csv_path)
    assert session_names, "no session names found in %s" % csv_path

    not_opened, failures = run_script_in_existing_sessions_scenario(
        context, session_names, CSV_TIMINGS_LABEL, CSV_SCRIPT_PATH,
        CSV_OUTPUT_DIR, CSV_OUTPUT_FILE_NAME, SCRIPT_TIMEOUT_MS, user=CSV_SESSIONS_USER,
    )
    assert not not_opened, "sessions that could not be opened: %s" % not_opened
    assert not failures, "script runs that did not finish: %s" % failures
