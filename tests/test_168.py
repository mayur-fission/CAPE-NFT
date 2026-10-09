"""US-168: RSTUDIO_SESSION_COUNT new RStudio Pro sessions, each in its own tab,
source the high-throughput R script concurrently and every run is timed.
Each run must finish within HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS and
create OUTPUT_FILE_NAME in OUTPUT_DIR/<session name> (deleted afterwards).
Every session created is force-quit afterwards.
"""
import pytest

from common.config import env
from common.rstudio_workbenchjob import DEFAULT_WORKBENCH_JOB_SCRIPT
from common.script_run_scenarios import run_script_in_new_sessions_scenario

pytestmark = pytest.mark.rstudio_local

HTP_SCRIPT_PATH = env("HIGH_THROUGHPUT_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", required=True))
TEST_DURATION_SECONDS = float(env("HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS", "180"))
OUTPUT_DIR = env("HIGH_THROUGHPUT_JOB_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data_10kb.csv"
# Own name prefix, so this test can run at the same time as the other
# high-throughput tests (see run_script_in_new_sessions_scenario()).
SESSION_NAME_PREFIX = "168_AUTO_SESSION_"


def test_high_throughput_script_concurrent_session_timed_168(context):
    """Writes evidence/rstudio_script_timings_high_throughput_multiple_sessions.csv.

    Flow: login -> launch new sessions, one per tab -> setwd to
    OUTPUT_DIR/<session name> -> run script in all consoles at once -> check
    and delete output files -> quit sessions
    """
    failures = run_script_in_new_sessions_scenario(
        context, SESSION_COUNT, "high_throughput_multiple_sessions", HTP_SCRIPT_PATH,
        OUTPUT_DIR, OUTPUT_FILE_NAME, int(TEST_DURATION_SECONDS * 1000), per_session_dir=True,
        name_prefix=SESSION_NAME_PREFIX,
    )
    assert not failures, "script runs that did not finish: %s" % failures
