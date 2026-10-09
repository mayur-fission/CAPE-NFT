"""US-167: one new RStudio Pro session sources the high-throughput R script in
its console and the run is timed. The run must finish within
HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS and create OUTPUT_FILE_NAME in
OUTPUT_DIR (deleted afterwards). The session is force-quit afterwards.
"""
import pytest

from common.config import env
from common.rstudio_workbenchjob import DEFAULT_WORKBENCH_JOB_SCRIPT
from common.script_run_scenarios import run_script_in_new_sessions_scenario

pytestmark = pytest.mark.rstudio_local

HTP_SCRIPT_PATH = env("HIGH_THROUGHPUT_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
TEST_DURATION_SECONDS = float(env("HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS", "180"))
OUTPUT_DIR = env("HIGH_THROUGHPUT_JOB_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data_10kb.csv"
# Own name prefix, so this test can run at the same time as the other
# high-throughput tests (see run_script_in_new_sessions_scenario()).
SESSION_NAME_PREFIX = "167_AUTO_SESSION_"


def test_high_throughput_script_single_session_timed_167(context):
    """Writes evidence/rstudio_script_timings_high_throughput_single_session.csv.

    Flow: login -> launch new session -> setwd to OUTPUT_DIR -> run script
    -> check and delete output file -> quit session
    """
    failures = run_script_in_new_sessions_scenario(
        context, 1, "high_throughput_single_session", HTP_SCRIPT_PATH,
        OUTPUT_DIR, OUTPUT_FILE_NAME, int(TEST_DURATION_SECONDS * 1000), name_prefix=SESSION_NAME_PREFIX,
    )
    assert not failures, "script runs that did not finish: %s" % failures
