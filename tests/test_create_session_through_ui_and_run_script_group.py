"""Mixed workloads on sessions created through the Workbench UI (New Session
dialog): one session per name in each group CSV
(testdata/group_<test|a|b|c>_sessions.csv), each launched in its own tab
and driven at the same time for TEST_DURATION_S:

- test: source SCRIPT_PATH in the console (must leave OUTPUT_FILE_NAME in
        OUTPUT_DIR/<session name>)
- a:    list.files(OUTPUT_DIR), repeated LIST_FILES_INTERVAL_S after each answer
- b:    BACKGROUND_JOB_SCRIPT_PATH as an RStudio background job (must not
        fail)
- c:    nothing - the session is left idle

The same workloads as test_create_session_through_api_and_run_script_group.py,
which creates the sessions through the API instead. Workload helpers are in
common/api_ui_combo.py.
"""
import pytest

from common.api_helper import group_sessions_csv_paths
from common.api_ui_combo import (
    create_sessions_in_ui_from_csvs,
    quit_ui_created_sessions,
    run_group_workloads,
    stop_jobs_and_close_tabs,
)
from common.config import env
from common.script_timings import script_timings_csv_path, write_script_timings_csv

pytestmark = pytest.mark.rstudio_local

CSV_SESSIONS_USER = int(env("CSV_SESSIONS_USER", "1"))
GROUP_CSVS = group_sessions_csv_paths(("test", "a", "b", "c"))
TIMINGS_LABEL = "new_ui_sessions_U%d" % CSV_SESSIONS_USER
LAUNCH_ATTEMPTS = int(env("GROUP_SESSIONS_UI_LAUNCH_ATTEMPTS", "3"))
# How long one launch may take to reach the IDE before it is retried, and
# the wait before a retry (which reopens the session if it was created).
LAUNCH_TIMEOUT_MS = int(env("GROUP_SESSIONS_UI_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("GROUP_SESSIONS_UI_LAUNCH_RETRY_WAIT_S", "15"))
SCRIPT_PATH = env("CSV_SESSIONS_SCRIPT", "/home/posit/batch_mayur/generate_10kb_csv.R")
OUTPUT_DIR = env("CSV_SESSIONS_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data_10kb.csv"
TEST_DURATION_S = int(env("GROUP_SESSIONS_TEST_DURATION_S", "180"))
LIST_FILES_INTERVAL_S = int(env("GROUP_A_LIST_FILES_INTERVAL_S", "30"))
BACKGROUND_JOB_SCRIPT_PATH = env("GROUP_B_BACKGROUND_JOB_SCRIPT", "/home/posit/batch_mayur/git_status_5sec.R")


def test_ui_created_session_groups_run_mixed_workloads(context):
    """Every group's workload (see the module docstring) succeeds while all
    groups run at once. Timings go to
    evidence/rstudio_script_timings_new_ui_sessions_U<user>.csv. Jobs, tabs
    and the sessions this test created are cleaned up even on failure.

    Flow: login -> quit existing sessions with the CSV names -> launch each
    session from the UI in its own tab -> start background jobs (b) -> run
    script (test) while polling list.files (a) -> keep going until
    TEST_DURATION_S -> check jobs -> close tabs -> quit the
    created sessions with the CSV names from the session list
    """
    home_url = before_ids = None
    launched, runs, groups = [], [], {}
    try:
        home_url, before_ids, names_by_csv, launched, failed = create_sessions_in_ui_from_csvs(
            context, CSV_SESSIONS_USER, GROUP_CSVS.values(), attempts=LAUNCH_ATTEMPTS,
            launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
        )
        assert not failed, "sessions that failed to launch: %s" % failed
        empty = [path for path, names in names_by_csv.items() if not names]
        assert not empty, "no session names found in %s" % empty

        by_name = {s.session_name: s for s in launched}
        groups = {g: [by_name[name] for name in names_by_csv[path]] for g, path in GROUP_CSVS.items()}
        run_group_workloads(
            groups, runs, TIMINGS_LABEL, TEST_DURATION_S, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILE_NAME,
            BACKGROUND_JOB_SCRIPT_PATH, LIST_FILES_INTERVAL_S, background_jobs=True,
        )
    finally:
        write_script_timings_csv(script_timings_csv_path(TIMINGS_LABEL), runs)
        # Background jobs end when their sessions are quit below.
        stop_jobs_and_close_tabs(launched)
        if home_url is not None:
            quit_ui_created_sessions(context, home_url, before_ids, GROUP_CSVS.values())
