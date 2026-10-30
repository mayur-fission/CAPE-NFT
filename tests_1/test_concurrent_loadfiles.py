"""Like test_concurrent_file_crud.py, but each session repeats a console
command instead of the file steps: LOAD_FILES_SESSIONS sessions (default 2)
launched from the UI, named LOAD_FILES_SESSION_1 .. _<N>, spread over
LOAD_FILES_BROWSERS browsers (blank = one browser per 5 sessions, as in the
other concurrent tests), all logged in as one user. Once every browser has
launched its sessions, each session sends LOAD_FILES_COMMAND (default
list.files(RSTUDIO_WORKING_DIR)) to its console, waits for the answer,
waits LOAD_FILES_WAIT_S (default 30 s) and sends it again, until
LOAD_FILES_DURATION_S is up (common/concurrent_load_files.py).

The command is typed into each tab without waiting for its answer, so all
tabs of a browser call it at the same time.

Per-call timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
(Script = the command, Loop = the call number) and the overall time to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per call (common/session_time_report.py), next to the CSV, all
attached to Allure. Set LOAD_FILES_THRESHOLD_S to mark a slow-call limit on
the charts.
"""
import time

import pytest

from common.concurrent_load_files import call_summary, run_load_files_in_concurrent_sessions
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.rstudio_console_commands import r_string
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_actions import WORKING_DIR
from common.session_naming_logic import LOAD_FILES_SESSION_PREFIX, concurrent_session_names
from common.session_time_report import describe_run, generate_session_time_report
from config.config import env

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("LOAD_FILES_SESSIONS", "2"))
# Blank = one browser per 5 sessions, as in the other concurrent tests
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("LOAD_FILES_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=LOAD_FILES_SESSION_PREFIX)
# The command each session repeats (blank = list.files(RSTUDIO_WORKING_DIR)).
COMMAND = env("LOAD_FILES_COMMAND") or ("list.files(%s)" % r_string(WORKING_DIR) if WORKING_DIR else "")
# Its name in the CSV and charts ("list.files"): the CSV keeps only the part
# after the last "/", which would cut list.files("/home/posit") to posit")
CALL_NAME = COMMAND.split("(", 1)[0].strip() or COMMAND
# How long to keep sending it, and the wait after each answer.
DURATION_S = float(env("LOAD_FILES_DURATION_S", "600"))
WAIT_S = float(env("LOAD_FILES_WAIT_S", "30"))
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "load_files_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-call limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("LOAD_FILES_THRESHOLD_S") or 0) or None


def test_concurrent_loadfiles(browser_name, browser_type_launch_args, browser_context_args):
    """Every session's repeated console calls answer.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> console ready, setwd -> wait for all
    browsers -> send the command, wait for the answer, wait LOAD_FILES_WAIT_S,
    repeat until LOAD_FILES_DURATION_S is up -> wait for all -> close tabs ->
    quit created sessions
    """
    assert SESSION_COUNT >= 1, "LOAD_FILES_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    if not COMMAND:
        pytest.skip("neither LOAD_FILES_COMMAND nor RSTUDIO_WORKING_DIR is set - nothing to list")
    records, errors = run_load_files_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, COMMAND, CALL_NAME, DURATION_S, WAIT_S, stagger_s=BROWSER_STAGGER_S,
        attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
    )
    print("\n[rstudio-local] %s: %d call(s) over %d session(s); %s" % (
        TIMINGS_LABEL, len([r for r in records if "loop" in r]), SESSION_COUNT, call_summary(records, CALL_NAME)))
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, records)
    summarize_concurrent_runs(records, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Time taken per %s call (every %.0fs after the answer)" % (COMMAND, WAIT_S),
        threshold=THRESHOLD_S, browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=COMMAND, group_noun="call",
    )
    assert not errors, "\n".join(errors)
