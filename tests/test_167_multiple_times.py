"""US-167, repeated: the test_167 run (one new RStudio Pro session sources the
high-throughput R script in its console, timed) done HTP_REPEAT_RUNS times
in a row, waiting HTP_REPEAT_WAIT_S seconds between runs. Each run must
finish within HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS and create
OUTPUT_FILE_NAME in OUTPUT_DIR (deleted afterwards); its session is
force-quit afterwards. Every run gets a fresh browser context, so it logs
in from scratch like test_167. A failed run does not stop the later ones -
the test fails at the end if any run failed.
"""
import time

import pytest

from common.config import env
from common.rstudio_workbenchjob import DEFAULT_WORKBENCH_JOB_SCRIPT
from common.script_run_scenarios import run_script_in_new_sessions_scenario

pytestmark = pytest.mark.rstudio_local

HTP_SCRIPT_PATH = env("HIGH_THROUGHPUT_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
TEST_DURATION_SECONDS = float(env("HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS", "180"))
OUTPUT_DIR = env("HIGH_THROUGHPUT_JOB_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data_10kb.csv"
RUNS = int(env("HTP_REPEAT_RUNS", "3"))
WAIT_BETWEEN_RUNS_S = float(env("HTP_REPEAT_WAIT_S", "30"))
# Own name prefix, so this test can run at the same time as test_167 and the
# other high-throughput tests (see run_script_in_new_sessions_scenario()).
SESSION_NAME_PREFIX = "167M_AUTO_SESSION_"


def test_high_throughput_script_single_session_timed_167_multiple_times(new_context):
    """Writes evidence/rstudio_script_timings_high_throughput_single_session_run<N>.csv
    per run.

    Flow, RUNS times: (wait WAIT_BETWEEN_RUNS_S, except before the first run)
    -> login -> launch new session -> setwd to OUTPUT_DIR -> run script
    -> check and delete output file -> quit session
    """
    assert RUNS >= 1, "HTP_REPEAT_RUNS must be at least 1, got %s" % RUNS
    failed = {}
    for run in range(1, RUNS + 1):
        if run > 1 and WAIT_BETWEEN_RUNS_S > 0:
            print("\n[rstudio-local] waiting %gs before run %d/%d" % (WAIT_BETWEEN_RUNS_S, run, RUNS))
            time.sleep(WAIT_BETWEEN_RUNS_S)
        print("\n[rstudio-local] run %d/%d" % (run, RUNS))
        context = new_context()
        try:
            failures = run_script_in_new_sessions_scenario(
                context, 1, "high_throughput_single_session_run%d" % run, HTP_SCRIPT_PATH,
                OUTPUT_DIR, OUTPUT_FILE_NAME, int(TEST_DURATION_SECONDS * 1000), name_prefix=SESSION_NAME_PREFIX,
            )
        except Exception as exc:
            failures = "error: %s" % exc
        finally:
            context.close()
        if failures:
            print("\n[rstudio-local] run %d/%d failed: %s" % (run, RUNS, failures))
            failed[run] = failures
    print("\n[rstudio-local] %d/%d runs passed" % (RUNS - len(failed), RUNS))
    assert not failed, "%d of %d runs failed: %s" % (len(failed), RUNS, failed)
