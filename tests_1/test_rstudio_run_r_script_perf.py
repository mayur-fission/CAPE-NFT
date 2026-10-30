"""R script run time in brand-new Run_R_session_<N> RStudio Pro sessions:
sourced once or RUN_COUNT times, by one user or CONCURRENT_USERS users at once
(each in its own isolated browser), and force-quits every session created.

The script sourced by R_SOURCE_COMMAND_NEW_SESSIONS (.env) must already
exist in RSTUDIO_WORKING_DIR on the server.
"""
import pytest

from config.config import env, env_path
from config.config import is_headless, optional_int
from common.session_actions import login_and_snapshot
from common.result_printing import describe_script_runs, failed, print_results, print_script_result
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_concurrent import concurrent_launch_and_run_script
from common.session_scenarios import launch_and_run_script

pytestmark = pytest.mark.rstudio_local

SOURCE_COMMAND = env_path("R_SOURCE_COMMAND_NEW_SESSIONS", "RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND")
SCRIPT_TIMEOUT_MS = int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))

# Runs per session (back to back) and the pause between runs (never after the last).
RUN_COUNT = int(env("RSTUDIO_RUN_SCRIPT_COUNT", "3"))
THINK_TIME_SECONDS = float(env("RSTUDIO_RUN_SCRIPT_THINK_TIME_SECONDS", "0"))

# Concurrent variant: simulated users at once, and an optional cap on parallel workers.
CONCURRENT_USERS = int(env("RSTUDIO_RUN_SCRIPT_CONCURRENT_USERS", "3"))
CONCURRENT_MAX_WORKERS = env("RSTUDIO_RUN_SCRIPT_CONCURRENT_MAX_WORKERS")


def test_new_session_run_r_script_once(page):
    """One new session sources SOURCE_COMMAND once.
    Writes evidence/rstudio_script_timings_new_session_single_run.csv.

    Flow: login -> launch new session -> setwd -> run script ->
    quit session
    """
    home_url, before_ids = login_and_snapshot(page)
    script_runs = []

    try:
        result = launch_and_run_script(
            page, home_url, source_command=SOURCE_COMMAND, timeout_ms=SCRIPT_TIMEOUT_MS, run_count=1,
            script_runs=script_runs,
        )
        print_script_result("single-run", result, SOURCE_COMMAND)
        assert result["run_elapsed_s"][0] > 0

    finally:
        write_script_timings_csv(script_timings_csv_path("new_session_single_run"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def test_new_session_run_r_script_repeatedly(page):
    """One new session sources SOURCE_COMMAND RUN_COUNT times back to back,
    pausing THINK_TIME_SECONDS between runs.
    Writes evidence/rstudio_script_timings_new_session_multiple_runs.csv.

    Flow: login -> launch new session -> setwd -> run script RUN_COUNT times
    -> quit session
    """
    home_url, before_ids = login_and_snapshot(page)
    script_runs = []

    try:
        result = launch_and_run_script(
            page, home_url, source_command=SOURCE_COMMAND, timeout_ms=SCRIPT_TIMEOUT_MS,
            run_count=RUN_COUNT, think_time_s=THINK_TIME_SECONDS, script_runs=script_runs,
        )
        print_script_result("multiple-runs", result, SOURCE_COMMAND)
        assert len(result["run_elapsed_s"]) == RUN_COUNT
        assert all(e > 0 for e in result["run_elapsed_s"])

    finally:
        write_script_timings_csv(script_timings_csv_path("new_session_multiple_runs"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def test_concurrent_users_new_sessions_run_r_script_once(page, request):
    """CONCURRENT_USERS users, each in their own browser, launch a new session
    at the same time and source SOURCE_COMMAND once.
    Writes evidence/rstudio_script_timings_new_sessions_concurrent_single_run.csv.

    Flow: login -> per user in parallel: open browser -> login -> launch new
    session -> setwd -> run script -> quit sessions
    """
    home_url, before_ids = login_and_snapshot(page)
    script_runs = []

    try:
        results, run_elapsed = concurrent_launch_and_run_script(
            page,
            count=CONCURRENT_USERS,
            source_command=SOURCE_COMMAND,
            timeout_ms=SCRIPT_TIMEOUT_MS,
            run_count=1,
            max_workers=optional_int(CONCURRENT_MAX_WORKERS),
            headless=is_headless(request),
            script_runs=script_runs,
        )
        print_results(results, CONCURRENT_USERS, "concurrent single-run user", describe_script_runs(SOURCE_COMMAND))

        failures = failed(results)
        assert not failures, "one or more concurrent users failed: %s" % failures

    finally:
        write_script_timings_csv(script_timings_csv_path("new_sessions_concurrent_single_run"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def test_concurrent_users_new_sessions_run_r_script_repeatedly(page, request):
    """CONCURRENT_USERS users, each in their own browser, launch a new session
    at the same time and source SOURCE_COMMAND RUN_COUNT times, pausing
    THINK_TIME_SECONDS between runs.
    Writes evidence/rstudio_script_timings_new_sessions_concurrent_multiple_runs.csv.

    Flow: login -> per user in parallel: open browser -> login -> launch new
    session -> setwd -> run script RUN_COUNT times -> quit
    sessions
    """
    home_url, before_ids = login_and_snapshot(page)
    script_runs = []

    try:
        results, run_elapsed = concurrent_launch_and_run_script(
            page,
            count=CONCURRENT_USERS,
            source_command=SOURCE_COMMAND,
            timeout_ms=SCRIPT_TIMEOUT_MS,
            think_time_s=THINK_TIME_SECONDS,
            run_count=RUN_COUNT,
            max_workers=optional_int(CONCURRENT_MAX_WORKERS),
            headless=is_headless(request),
            script_runs=script_runs,
        )
        print_results(results, CONCURRENT_USERS, "concurrent multiple-runs user", describe_script_runs(SOURCE_COMMAND))

        failures = failed(results)
        assert not failures, "one or more concurrent users failed: %s" % failures

    finally:
        write_script_timings_csv(script_timings_csv_path("new_sessions_concurrent_multiple_runs"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)
