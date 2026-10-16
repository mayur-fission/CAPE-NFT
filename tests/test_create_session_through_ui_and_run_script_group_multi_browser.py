"""test_create_session_through_ui_and_run_script_group.py in
GROUP_SESSIONS_UI_BROWSERS browsers at once, all logged in as one user.
Each browser launches every name in testdata/group_<test|a|b|c>_sessions.csv
from the UI, one tab per session, prefixed B<n>_ (TEST_USER_1 ->
B1_TEST_USER_1, B2_TEST_USER_1, ...). All sessions then run for
TEST_DURATION_S:

- test: source SCRIPT_PATH (must leave OUTPUT_FILE_NAME in
        OUTPUT_DIR/<session name>)
- a:    list.files(OUTPUT_DIR), repeated LIST_FILES_INTERVAL_S after each answer
- b:    BACKGROUND_JOB_SCRIPT_PATH as an RStudio background job (must not fail)
- c:    idle

Several machines with the same user (Jenkins USE_EC2_NODES): each sets
GROUP_SESSIONS_UI_NODE_PREFIX (N1_, N2_, ...) so it only touches its own
sessions (N1_B1_TEST_USER_1, ...), and GROUP_SESSIONS_UI_START_AT_EPOCH so
the machines' logins don't clash.
"""
import pytest

from common.api_helper import group_sessions_csv_paths
from common.api_ui_combo import browser_name_prefixes, run_group_workloads_across_browsers, wait_until_epoch
from common.config import env, env_path
from common.script_timings import script_timings_csv_path, write_script_timings_csv

pytestmark = pytest.mark.rstudio_local

CSV_SESSIONS_USER = int(env("CSV_SESSIONS_USER", "1"))
BROWSER_COUNT = int(env("GROUP_SESSIONS_UI_BROWSERS", "2"))
GROUP_CSVS = group_sessions_csv_paths(("test", "a", "b", "c"))
# Put in front of every browser prefix when several machines run this test
# as the same user, e.g. "N1_" -> N1_B1_, N1_B2_, ...
NODE_PREFIX = env("GROUP_SESSIONS_UI_NODE_PREFIX", "")
NAME_PREFIXES = [NODE_PREFIX + p for p in browser_name_prefixes(BROWSER_COUNT)]
# Unix time to wait for before the first browser starts (blank = start now).
START_AT_EPOCH = env("GROUP_SESSIONS_UI_START_AT_EPOCH") or None
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("GROUP_SESSIONS_UI_BROWSER_STAGGER_S", "30"))
TIMINGS_LABEL = "new_ui_sessions_%d_browsers_U%d%s" % (BROWSER_COUNT, CSV_SESSIONS_USER,
                                                        "_" + NODE_PREFIX.rstrip("_") if NODE_PREFIX else "")
LAUNCH_ATTEMPTS = int(env("GROUP_SESSIONS_UI_LAUNCH_ATTEMPTS", "3"))
# How long one launch may take to reach the IDE before it is retried, and
# the wait before a retry (which reopens the session if it was created).
LAUNCH_TIMEOUT_MS = int(env("GROUP_SESSIONS_UI_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("GROUP_SESSIONS_UI_LAUNCH_RETRY_WAIT_S", "15"))
SCRIPT_PATH = env_path("R_SCRIPT_GENERATE_CSV", "CSV_SESSIONS_SCRIPT")
OUTPUT_DIR = env_path("OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
OUTPUT_FILE_NAME = "generated_data_10kb.csv"
TEST_DURATION_S = int(env("GROUP_SESSIONS_TEST_DURATION_S", "180"))
LIST_FILES_INTERVAL_S = int(env("GROUP_A_LIST_FILES_INTERVAL_S", "30"))
BACKGROUND_JOB_SCRIPT_PATH = env_path("R_SCRIPT_GROUP_B_BACKGROUND_JOB", "GROUP_B_BACKGROUND_JOB_SCRIPT")


def test_ui_created_session_groups_run_mixed_workloads_in_multiple_browsers(
    browser_name, browser_type_launch_args, browser_context_args
):
    """Every group's workload succeeds in every browser. Browsers start
    BROWSER_STAGGER_S apart and wait for each other before the workloads
    (skipped everywhere if any browser failed to launch) and before cleanup.
    Timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv; tabs,
    jobs and created sessions are cleaned up even on failure.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all -> workloads -> wait for all
    -> close tabs -> quit created sessions -> close browser
    """
    assert BROWSER_COUNT >= 1, "GROUP_SESSIONS_UI_BROWSERS must be at least 1, got %d" % BROWSER_COUNT
    wait_until_epoch(START_AT_EPOCH, NODE_PREFIX or "this machine")
    runs, errors = run_group_workloads_across_browsers(
        browser_name, browser_type_launch_args, browser_context_args, CSV_SESSIONS_USER, GROUP_CSVS,
        NAME_PREFIXES, TIMINGS_LABEL, stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS,
        launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
        duration_s=TEST_DURATION_S, script_path=SCRIPT_PATH, output_dir=OUTPUT_DIR,
        output_file_name=OUTPUT_FILE_NAME, job_script_path=BACKGROUND_JOB_SCRIPT_PATH,
        list_files_interval_s=LIST_FILES_INTERVAL_S, background_jobs=True,
    )
    write_script_timings_csv(script_timings_csv_path(TIMINGS_LABEL), runs)
    assert not errors, "\n".join(errors)
