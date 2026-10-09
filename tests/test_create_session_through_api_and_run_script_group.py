"""Mixed workloads on sessions created through the Workbench API: one session
per name in each group CSV (testdata/group_<test|a|b|c>_sessions.csv), all
opened in the browser and driven at the same time for TEST_DURATION_S:

- test: source SCRIPT_PATH in the console (must leave OUTPUT_FILE_NAME in
        OUTPUT_DIR/<session name>)
- a:    list.files(OUTPUT_DIR), repeated LIST_FILES_INTERVAL_S after each answer
- b:    WORKBENCH_JOB_SCRIPT_PATH as a Workbench job (must not fail)
- c:    nothing - the session is left idle

Workload helpers are in common/api_ui_combo.py.
"""
import pytest

from common.api_helper import group_sessions_csv_paths, session_ids_csv_path
from common.api_ui_combo import (
    clean_up,
    create_sessions_from_csvs,
    open_sessions,
    run_group_workloads,
)
from common.config import env
from common.script_timings import script_timings_csv_path, write_script_timings_csv

pytestmark = pytest.mark.rstudio_local

CSV_SESSIONS_USER = int(env("CSV_SESSIONS_USER", "1"))
GROUP_CSVS = group_sessions_csv_paths(("test", "a", "b", "c"))
SESSION_IDS_CSV = session_ids_csv_path(CSV_SESSIONS_USER)
TIMINGS_LABEL = "new_api_sessions_U%d" % CSV_SESSIONS_USER
SCRIPT_PATH = env("CSV_SESSIONS_SCRIPT", "/home/posit/batch_mayur/generate_10kb_csv.R")
OUTPUT_DIR = env("CSV_SESSIONS_OUTPUT_DIR", "/home/posit")
OUTPUT_FILE_NAME = "generated_data_10kb.csv"
TEST_DURATION_S = int(env("GROUP_SESSIONS_TEST_DURATION_S", "300"))
LIST_FILES_INTERVAL_S = int(env("GROUP_A_LIST_FILES_INTERVAL_S", "30"))
WORKBENCH_JOB_SCRIPT_PATH = env("GROUP_B_WORKBENCH_JOB_SCRIPT", "/home/posit/sample_run_sleep.R")


def test_api_created_session_groups_run_mixed_workloads(context):
    """Every group's workload (see the module docstring) succeeds while all
    groups run at once. Timings go to
    evidence/rstudio_script_timings_new_api_sessions_U<user>.csv; session ids
    to testdata/session_ids_U<user>.csv. Jobs, tabs and sessions are cleaned
    up even on failure.

    Flow: create sessions via API -> login -> open each session in a tab ->
    start Workbench jobs (b) -> run script (test) while polling list.files
    (a) -> keep going until TEST_DURATION_S -> check jobs -> stop jobs ->
    close tabs -> quit sessions via API
    """
    created, launched, runs, groups = [], [], [], {}
    try:
        names_by_csv, created, failed = create_sessions_from_csvs(
            GROUP_CSVS.values(), CSV_SESSIONS_USER, SESSION_IDS_CSV
        )
        assert not failed, "sessions that failed to launch: %s" % failed
        empty = [path for path, names in names_by_csv.items() if not names]
        assert not empty, "no session names found in %s" % empty

        launched = open_sessions(context, CSV_SESSIONS_USER, [s["session_name"] for s in created])
        by_name = {s.session_name: s for s in launched}
        groups = {g: [by_name[name] for name in names_by_csv[path]] for g, path in GROUP_CSVS.items()}
        run_group_workloads(
            groups, runs, TIMINGS_LABEL, TEST_DURATION_S, SCRIPT_PATH, OUTPUT_DIR, OUTPUT_FILE_NAME,
            WORKBENCH_JOB_SCRIPT_PATH, LIST_FILES_INTERVAL_S,
        )
    finally:
        write_script_timings_csv(script_timings_csv_path(TIMINGS_LABEL), runs)
        clean_up(launched, created, CSV_SESSIONS_USER, groups.get("b", ()), WORKBENCH_JOB_SCRIPT_PATH)

