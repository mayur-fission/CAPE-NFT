"""Like test_create_edit_open_delete_text_file.py, but in many sessions at
once, the way test_concurrent_sessions_10min.py runs its script:
FILE_CRUD_SESSIONS sessions (default 2) launched from the UI, named
FILE_CRUD_SESSION_1 .. _<N>, spread over FILE_CRUD_BROWSERS browsers (blank =
one browser per 5 sessions, as in the other concurrent tests), all logged in
as one user. Once every browser has launched its sessions, each session
loops for TEXT_FILE_LOOP_DURATION_S: create and save a text file, open it,
edit and save it, delete it, wait TEXT_FILE_LOOP_WAIT_S
(common/concurrent_file_crud.py).

The steps drive the IDE with the mouse and keyboard and a browser runs in
one thread, so tabs of the same browser take turns (one tab's loop runs
while the others wait out their TEXT_FILE_LOOP_WAIT_S); set
FILE_CRUD_BROWSERS to the session count for every session to loop at the
same moment.

Per-step timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
(Script = the step, Loop = the loop number) and the overall time to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per step, grouped by step (common/session_time_report.py), next to
the CSV, all attached to Allure. Set FILE_CRUD_THRESHOLD_S to mark a
slow-step limit on the charts.
"""
import time

import pytest

from common.concurrent_file_crud import run_file_crud_in_concurrent_sessions
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_actions import WORKING_DIR
from common.session_naming_logic import FILE_CRUD_SESSION_PREFIX, concurrent_session_names
from common.session_time_report import describe_run, generate_session_time_report
from common.text_file_crud import LOOP_DURATION_S, LOOP_WAIT_S, STEPS, step_summary
from config.config import env

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("FILE_CRUD_SESSIONS", "2"))
# Blank = one browser per 5 sessions, as in the other concurrent tests
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("FILE_CRUD_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=FILE_CRUD_SESSION_PREFIX)
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "file_crud_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))
# Slow-step limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("FILE_CRUD_THRESHOLD_S") or 0) or None


def test_concurrent_file_crud(browser_name, browser_type_launch_args, browser_context_args):
    """Every session's create, open, edit and delete loops work.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> setwd, Files pane, auto_test folder ->
    wait for all browsers -> loop create / open / edit / delete until
    TEXT_FILE_LOOP_DURATION_S is up -> wait for all -> close tabs -> quit
    created sessions
    """
    assert SESSION_COUNT >= 1, "FILE_CRUD_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    if not WORKING_DIR:
        pytest.skip("RSTUDIO_WORKING_DIR not set - the text files are saved under it")
    records, errors = run_file_crud_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, LOOP_DURATION_S, LOOP_WAIT_S, stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS,
        launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
    )
    print("\n[rstudio-local] %s: %d loop(s) over %d session(s); %s" % (
        TIMINGS_LABEL, len({(r["session_name"], r.get("loop")) for r in records if "loop" in r}), SESSION_COUNT,
        step_summary(records)))
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, records)
    summarize_concurrent_runs(records, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Time taken per file step (concurrent create / open / edit / delete)",
        threshold=THRESHOLD_S, browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=", ".join(STEPS) + " steps", group_noun="step",
    )
    assert not errors, "\n".join(errors)
