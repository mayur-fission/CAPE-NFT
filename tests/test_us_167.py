
import posixpath
import time

import pytest

from common.config import env
from common.rstudio_session_helper import row_ids
from common.rstudio_workbenchjob import (
    DEFAULT_WORKBENCH_JOB_SCRIPT,
    launch_sessions_and_run_script,
)
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions

pytestmark = pytest.mark.rstudio_local

HTP_SCRIPT_PATH = env("HIGH_THROUGHPUT_JOB_SCRIPT", DEFAULT_WORKBENCH_JOB_SCRIPT)
JOB_NAME = posixpath.basename(HTP_SCRIPT_PATH)
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", required=True))
TEST_DURATION_SECONDS = float(env("RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS", "300"))
# The script writes OUTPUT_FILE_NAME relative to its working directory, so
# setwd() to OUTPUT_DIR (or OUTPUT_DIR/<session name> with several sessions)
# before sourcing it.
OUTPUT_DIR = env("HIGH_THROUGHPUT_JOB_OUTPUT_DIR", "/fsx/data/batch_mayur")
OUTPUT_FILE_NAME = "generated_data.csv"
SCRIPT_TIMEOUT_MS = int(env("HIGH_THROUGHPUT_JOB_TIMEOUT_MS", "1800000"))


def _run_script_in_tabs_and_time(context, session_count, label, per_session_dir=False):
    """Launch `session_count` sessions, each in its own tab, setwd to
    OUTPUT_DIR (OUTPUT_DIR/<session name> with per_session_dir), source
    HTP_SCRIPT_PATH in every console at once, time each run and check that
    it created OUTPUT_FILE_NAME there, then delete it (/fsx/data is close to
    full; each file is ~100 MB) along with any per-session folder. Writes the
    per-session timings to evidence/rstudio_script_timings_<label>.csv and
    quits every session created.
    """
    home_page = context.new_page()
    home_url = login_to_posit_workbench(home_page)
    before_ids = row_ids(home_page)
    runs = []

    started = time.time()
    error = None
    try:
        runs = launch_sessions_and_run_script(
            context, home_url, session_count, HTP_SCRIPT_PATH,
            working_dir=OUTPUT_DIR, timeout_ms=SCRIPT_TIMEOUT_MS,
            output_path=OUTPUT_FILE_NAME, per_session_dir=per_session_dir, delete_output=True,
        )
        failures = ["%s: %s" % (r["session_name"], r["status"]) for r in runs if r["status"] != "ok"]
        print(
            "\n[rstudio-local] %s: %d of %d session(s) ran %s successfully, %.2fs in total"
            % (label, session_count - len(failures), session_count, HTP_SCRIPT_PATH, time.time() - started)
        )
        assert not failures, "script runs that did not finish: %s" % failures
    except Exception as exc:
        error = exc
        raise
    finally:
        write_script_timings_csv(script_timings_csv_path(label), runs)
        try:
            cleanup_and_verify_sessions(home_page, home_url, before_ids)
        except Exception as cleanup_exc:
            # Don't let a cleanup failure (e.g. the browser was closed) hide
            # the error that failed the test.
            if error is None:
                raise
            print("\n[rstudio-local] %s: cleanup also failed: %s" % (label, cleanup_exc))


def test_create_session_run_high_throughput_script_in_console_timed(context):
    """One session in its own tab: setwd to OUTPUT_DIR, source
    HTP_SCRIPT_PATH in the console and capture the time it takes to finish.
    The script saves OUTPUT_DIR/generated_data.csv.
    """
    _run_script_in_tabs_and_time(context, 1, "high_throughput_single_session")