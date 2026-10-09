"""The mixed workloads of test_create_session_through_ui_and_run_script_group.py
repeated in several browsers: GROUP_SESSIONS_UI_BROWSERS separate browsers
are opened, the user logs in in each, and every browser launches one session
per name in the group CSVs (testdata/group_<test|a|b|c>_sessions.csv), each
in its own tab, with the browser's prefix in front of the name (B1_, B2_,
... - e.g. TEST_USER_1 becomes B1_TEST_USER_1 and B2_TEST_USER_1). With 5
names and 2 browsers that is 10 tabs, 5 per browser. All sessions are then
driven at the same time for TEST_DURATION_S:

- test: source SCRIPT_PATH in the console (must leave OUTPUT_FILE_NAME in
        OUTPUT_DIR/<session name>)
- a:    list.files(OUTPUT_DIR), repeated LIST_FILES_INTERVAL_S after each answer
- b:    BACKGROUND_JOB_SCRIPT_PATH as an RStudio background job (must not
        fail)
- c:    nothing - the session is left idle

Workload helpers are in common/api_ui_combo.py.
"""
import threading

import pytest

from common.api_helper import group_sessions_csv_paths
from common.api_ui_combo import (
    browser_name_prefixes,
    create_sessions_in_ui_across_browsers,
    quit_ui_created_sessions,
    run_group_workloads,
    run_in_parallel_browsers,
    stop_jobs_and_close_tabs,
)
from common.config import env
from common.script_timings import script_timings_csv_path, write_script_timings_csv

pytestmark = pytest.mark.rstudio_local

CSV_SESSIONS_USER = int(env("CSV_SESSIONS_USER", "1"))
BROWSER_COUNT = int(env("GROUP_SESSIONS_UI_BROWSERS", "2"))
GROUP_CSVS = group_sessions_csv_paths(("test", "a", "b", "c"))
NAME_PREFIXES = browser_name_prefixes(BROWSER_COUNT)
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("GROUP_SESSIONS_UI_BROWSER_STAGGER_S", "30"))
TIMINGS_LABEL = "new_ui_sessions_%d_browsers_U%d" % (BROWSER_COUNT, CSV_SESSIONS_USER)
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


def test_ui_created_session_groups_run_mixed_workloads_in_multiple_browsers(
    browser_name, browser_type_launch_args, browser_context_args
):
    """Every group's workload succeeds while all groups run at once, each
    CSV name launched once per browser (B<n>_ prefix). Timings go to
    evidence/rstudio_script_timings_new_ui_sessions_<browsers>_browsers_U<user>.csv.
    Jobs, tabs, browsers and the sessions this test created are cleaned up
    even on failure.

    Each browser runs in its own thread, started BROWSER_STAGGER_S after the
    previous one so their logins don't clash, so the browsers create and launch
    their sessions in parallel and independently of each other; they only
    wait for one another before the workloads (so all groups run at once)
    and before cleanup (so no browser quits its sessions while the others
    are still running their workloads).

    Flow, in every browser at once: open browser -> login -> quit existing
    sessions with its prefixed CSV names -> launch every CSV name with the
    browser's B<n>_ prefix from the UI in its own tab -> wait for the other
    browsers -> start background jobs (b) -> run script (test) while polling
    list.files (a) -> keep going until TEST_DURATION_S -> check jobs -> wait
    for the other browsers -> close tabs -> quit the created
    sessions from the session list -> close browser
    """
    assert BROWSER_COUNT >= 1, "GROUP_SESSIONS_UI_BROWSERS must be at least 1, got %d" % BROWSER_COUNT
    ready = threading.Barrier(BROWSER_COUNT)
    done = threading.Barrier(BROWSER_COUNT)
    launch_failed = set()

    def _browser_worker(context, prefix):
        return _run_browser(context, prefix, ready, done, launch_failed)

    results = run_in_parallel_browsers(
        browser_name, browser_type_launch_args, browser_context_args, NAME_PREFIXES, _browser_worker,
        barriers=(ready, done), stagger_s=BROWSER_STAGGER_S,
    )
    runs, errors = [], []
    for prefix, result, error in results:
        if result is not None:
            browser_runs, error = result
            runs += browser_runs
        if error is not None:
            errors.append("browser %s: %s" % (prefix, error))
    write_script_timings_csv(script_timings_csv_path(TIMINGS_LABEL), runs)
    assert not errors, "\n".join(errors)


def _run_browser(context, prefix, ready, done, launch_failed):
    """One browser's part of the test (see its docstring). Returns (runs,
    error): its run records and the error that failed it, else None."""
    home_url = before_ids = None
    launched, runs, groups, error = [], [], {}, None
    try:
        try:
            home_url, before_ids, names_by_csv, launched, failed = create_sessions_in_ui_across_browsers(
                [context], CSV_SESSIONS_USER, GROUP_CSVS.values(), prefixes=[prefix],
                attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
            )
            assert not failed, "sessions that failed to launch: %s" % failed
            empty = [path for path, names in names_by_csv.items() if not names]
            assert not empty, "no session names found in %s" % empty
            by_name = {s.session_name: s for s in launched}
            groups = {g: [by_name[name] for name in names_by_csv[path]] for g, path in GROUP_CSVS.items()}
        except Exception as exc:
            launch_failed.add(prefix)
            raise
        finally:
            print("\n[rstudio-local] browser %s: sessions launched, waiting for the other browsers" % prefix)
            ready.wait()
        if launch_failed:
            raise AssertionError("workloads skipped: launch failed in browser(s) %s" % sorted(launch_failed))
        run_group_workloads(
            groups, runs, "%s %s" % (TIMINGS_LABEL, prefix), TEST_DURATION_S, SCRIPT_PATH, OUTPUT_DIR,
            OUTPUT_FILE_NAME, BACKGROUND_JOB_SCRIPT_PATH, LIST_FILES_INTERVAL_S, background_jobs=True,
        )
    except Exception as exc:
        error = exc
    finally:
        try:
            done.wait()
        except threading.BrokenBarrierError:
            pass
        # Background jobs end when their sessions are quit below.
        stop_jobs_and_close_tabs(launched)
        if home_url is not None:
            quit_ui_created_sessions(context, home_url, before_ids, GROUP_CSVS.values(), [prefix])
    return runs, error
