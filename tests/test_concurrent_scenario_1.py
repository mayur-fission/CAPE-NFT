"""Scenario 1: a mix of the concurrent tests' workloads at once.
CONCURRENT_SESSIONS_SCENARIO sessions (default 5) launched from the UI, at
most 5 tabs per browser (one browser per 5 sessions, as in the other
concurrent tests), all logged in as one user. Every 5 sessions - one
browser's tabs - are:
  1 x file crud    (test_concurrent_file_crud): create / open / edit /
                   delete a text file, TEXT_FILE_LOOP_WAIT_S between loops
  1 x idle         (test_concurrent_idle): tab kept open, checked for drops
                   every CONCURRENT_IDLE_CHECK_INTERVAL_S
  1 x small 10min  (test_concurrent_sessions_10min_s): R_SCRIPT_SMALL_10MIN
                   sourced once, its output files checked
  2 x load files   (test_concurrent_loadfiles): LOAD_FILES_COMMAND repeated
                   LOAD_FILES_WAIT_S after each answer
and the same 1:1:1:2 ratio for more sessions (10 -> 2:2:2:4, ...; a count
that is not a multiple of 5 follows the order above for the rest). They are
named SCENARIO1_<workload>_<N> (common/concurrent_scenario.py).

Once every browser has launched its sessions, all workloads start together
and run for CONCURRENT_SESSIONS_SCENARIO_DURATION seconds (default 600); the
script runs to its end. Then every session is force-quit. Each workload's
own settings are those of its test (above).

Every record - file steps, command calls, idle holds, script runs - goes to
evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv (Script tells them apart),
the overall time to evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json,
idle session drops to evidence/session_drops_<TIMINGS_LABEL>.json, with the
session time charts grouped by workload (common/session_time_report.py), all
attached to Allure.
"""
import time

import pytest

from common.concurrent_load_files import call_summary
from common.concurrent_scenario import run_scenario_in_concurrent_sessions, scenario_session_names, workload_summary
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.rstudio_console_commands import r_string
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_actions import WORKING_DIR
from common.session_time_report import describe_run, generate_session_time_report
from common.text_file_crud import LOOP_WAIT_S, step_summary
from config.config import env, env_list, env_path

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("CONCURRENT_SESSIONS_SCENARIO", "5"))
# One browser per 5 sessions: never more than 5 tabs in a browser
BROWSER_COUNT = browsers_for(SESSION_COUNT)
SESSION_NAMES = scenario_session_names(SESSION_COUNT)
DURATION_S = float(env("CONCURRENT_SESSIONS_SCENARIO_DURATION", "600"))

# Load files: test_concurrent_loadfiles' settings
COMMAND = env("LOAD_FILES_COMMAND") or ("list.files(%s)" % r_string(WORKING_DIR) if WORKING_DIR else "")
CALL_NAME = COMMAND.split("(", 1)[0].strip() or COMMAND
CALL_WAIT_S = float(env("LOAD_FILES_WAIT_S", "30"))
# Idle: test_concurrent_idle's
IDLE_CHECK_INTERVAL_S = float(env("CONCURRENT_IDLE_CHECK_INTERVAL_S", "60"))
# Small 10min script: test_concurrent_sessions_10min_s'
SCRIPT_PATH = env("R_SCRIPT_SMALL_10MIN")
OUTPUT_DIR = env_path("OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                      "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
SCRIPT_OUTPUT_FOLDER = "pmx_10kb_output"
OUTPUT_FILES = env_list("CONCURRENT_SMALL_10MIN_OUTPUT_FILES", [
    "%s/%s" % (SCRIPT_OUTPUT_FOLDER, path) for path in (
        "data/nm_dataset.csv",
        "tables/exposure_summary.csv",
        "logs/run_log.txt",
        "logs/model_spec.txt",
        "logs/session_info.txt",
        "logs/run_summary.txt",
    )
])
RUN_SUMMARY_FILE = env("CONCURRENT_SMALL_10MIN_RUN_SUMMARY", "%s/logs/run_summary.txt" % SCRIPT_OUTPUT_FOLDER)
SCRIPT_TIMEOUT_MS = int(env("CONCURRENT_SMALL_10MIN_SCRIPT_TIMEOUT_MS", "900000"))
POLL_INTERVAL_MS = int(env("CONCURRENT_SMALL_10MIN_POLL_MS", "500"))
DELETE_OUTPUT = env("CONCURRENT_SMALL_10MIN_DELETE_OUTPUT", "true").lower() != "false"

# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "scenario_1_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-record limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("CONCURRENT_SESSIONS_SCENARIO_THRESHOLD_S") or 0) or None


def test_concurrent_scenario_1(browser_name, browser_type_launch_args, browser_context_args):
    """Every workload of the mix works, and no idle session drops.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> prepare each for its workload -> wait for
    all browsers -> source the script, and meanwhile loop the file steps,
    repeat the command and check the idle tabs, until
    CONCURRENT_SESSIONS_SCENARIO_DURATION is up -> wait for all -> close
    tabs -> quit created sessions
    """
    assert SESSION_COUNT >= 1, "CONCURRENT_SESSIONS_SCENARIO must be at least 1, got %d" % SESSION_COUNT
    if not WORKING_DIR:
        pytest.skip("RSTUDIO_WORKING_DIR not set - the file loops and the load files command work under it")
    if not SCRIPT_PATH:
        pytest.skip("R_SCRIPT_SMALL_10MIN is not set - set it in .env to the path of pmx_10kb_10min.R")
    if SESSION_COUNT % 5:
        print("\n[rstudio-local] note: %d sessions is not a multiple of 5 - the last browser gets a partial mix"
              % SESSION_COUNT)
    records, drops, errors = run_scenario_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, DURATION_S, LOOP_WAIT_S, COMMAND, CALL_NAME, CALL_WAIT_S, IDLE_CHECK_INTERVAL_S,
        SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILES, SCRIPT_TIMEOUT_MS, poll_interval_ms=POLL_INTERVAL_MS,
        delete_output=DELETE_OUTPUT, summary_file=RUN_SUMMARY_FILE or None, stagger_s=BROWSER_STAGGER_S,
        attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
    )
    print("\n[rstudio-local] %s: per workload\n%s\n  file steps: %s\n  %s" % (
        TIMINGS_LABEL, workload_summary(records), step_summary(records) or "none ok",
        call_summary(records, CALL_NAME)))
    if drops:
        print("\n[rstudio-local] %d idle session(s) dropped before the %.0fs were up:\n%s" % (
            len(drops), DURATION_S, "\n".join(
                "  %s - %s, seen in the %s %ss into the hold (%s)"
                % (d["session"], d["status"], d["detected_by"], d["seconds_after_idle_start"], d["time"])
                for d in drops)))
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, records)
    summarize_concurrent_runs(records, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Scenario 1: file crud, idle, small 10min script and %s per session" % CALL_NAME,
        threshold=THRESHOLD_S, browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script="file steps, idle, %s, %s" % (SCRIPT_PATH.rsplit("/", 1)[-1], CALL_NAME), group_noun="workload",
    )
    assert not drops, "%d idle session(s) dropped: %s" % (
        len(drops), ", ".join("%s (%s after %ss)" % (d["session"], d["status"], d["seconds_after_idle_start"])
                              for d in drops))
    assert not errors, "\n".join(errors)
