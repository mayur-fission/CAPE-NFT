"""Launch brand-new Run_R_session_<N> RStudio Pro session(s) and source an R
script in each console - once, RUN_COUNT times, or for CONCURRENT_USERS
users at once (each in its own isolated browser). Every test force-quits the
sessions it created.

The sourced script (default sample_10mb.R) must already exist in
RSTUDIO_WORKING_DIR on the server. Server/DB metrics are best-effort only.
"""
import pytest

from common.config import env
from common.helper_function import (
    capture_server_and_db_metrics,
    describe_script_runs,
    failed,
    is_headless,
    mean,
    optional_int,
    otel_sample,
    otel_samples,
    otel_samples_for_runs,
    print_results,
    print_script_result,
)
from common.otel_metrics import capture_otel_metrics
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_concurrent import concurrent_launch_and_run_script
from common.session_scenarios import launch_and_run_script
from common.rstudio_session_helper import row_ids

pytestmark = pytest.mark.rstudio_local

SOURCE_COMMAND = env("RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
SCRIPT_TIMEOUT_MS = int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))
METRICS_WINDOW_MINUTES = int(env("RSTUDIO_METRICS_WINDOW_MINUTES", "5"))

# Runs per session (back to back) and the pause between runs (never after the last).
RUN_COUNT = int(env("RSTUDIO_RUN_SCRIPT_COUNT", "3"))
THINK_TIME_SECONDS = float(env("RSTUDIO_RUN_SCRIPT_THINK_TIME_SECONDS", "0"))

# Concurrent variant: simulated users at once, and an optional cap on parallel workers.
CONCURRENT_USERS = int(env("RSTUDIO_RUN_SCRIPT_CONCURRENT_USERS", "3"))
CONCURRENT_MAX_WORKERS = env("RSTUDIO_RUN_SCRIPT_CONCURRENT_MAX_WORKERS")


def test_rstudio_new_session_single_run(page):
    """One new session, working directory set, script sourced once."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)
    script_runs = []

    try:
        result = launch_and_run_script(
            page, home_url, source_command=SOURCE_COMMAND, timeout_ms=SCRIPT_TIMEOUT_MS, run_count=1,
            script_runs=script_runs,
        )
        print_script_result("single-run", result, SOURCE_COMMAND)
        assert result["run_elapsed_s"][0] > 0

        capture_server_and_db_metrics(METRICS_WINDOW_MINUTES)
        capture_otel_metrics(
            [otel_sample(1, result["run_elapsed_s"][0], result["bytes_received"])],
            run_elapsed=result["launch_elapsed_s"] + result["run_elapsed_s"][0],
            scenario="new_session_single_run",
        )
    finally:
        write_script_timings_csv(script_timings_csv_path("new_session_single_run"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def test_rstudio_new_session_multiple_runs(page):
    """One new session, script sourced RUN_COUNT times back to back with
    THINK_TIME_SECONDS between runs."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)
    script_runs = []

    try:
        result = launch_and_run_script(
            page, home_url, source_command=SOURCE_COMMAND, timeout_ms=SCRIPT_TIMEOUT_MS,
            run_count=RUN_COUNT, think_time_s=THINK_TIME_SECONDS, script_runs=script_runs,
        )
        print_script_result("multiple-runs", result, SOURCE_COMMAND)
        assert len(result["run_elapsed_s"]) == RUN_COUNT
        assert all(e > 0 for e in result["run_elapsed_s"])

        capture_server_and_db_metrics(METRICS_WINDOW_MINUTES)
        capture_otel_metrics(
            otel_samples_for_runs(result),
            run_elapsed=result["launch_elapsed_s"] + sum(result["run_elapsed_s"]),
            scenario="new_session_multiple_runs",
        )
    finally:
        write_script_timings_csv(script_timings_csv_path("new_session_multiple_runs"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def test_rstudio_new_sessions_concurrent_single_run(page, request):
    """CONCURRENT_USERS users each launch a new session in their own browser
    at once and source the script once."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)
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

        capture_server_and_db_metrics(METRICS_WINDOW_MINUTES)
        capture_otel_metrics(
            otel_samples(results, lambda r: r["run_elapsed_s"][0]),
            run_elapsed=run_elapsed,
            scenario="new_sessions_concurrent_single_run",
        )
    finally:
        write_script_timings_csv(script_timings_csv_path("new_sessions_concurrent_single_run"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def test_rstudio_new_sessions_concurrent_multiple_runs(page, request):
    """CONCURRENT_USERS users each launch a new session in their own browser
    at once and source the script RUN_COUNT times, with THINK_TIME_SECONDS
    between runs."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)
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

        capture_server_and_db_metrics(METRICS_WINDOW_MINUTES)
        capture_otel_metrics(
            otel_samples(results, lambda r: mean(r["run_elapsed_s"])),
            run_elapsed=run_elapsed,
            scenario="new_sessions_concurrent_multiple_runs",
        )
    finally:
        write_script_timings_csv(script_timings_csv_path("new_sessions_concurrent_multiple_runs"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)
