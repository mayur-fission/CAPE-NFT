"""Source a large R script in already-existing RStudio Pro session(s) - one
(SESSION_NAME) or CONCURRENT_SESSION_NAMES at once, each in its own isolated
browser - and capture run time plus best-effort server/DB metrics.

Every session named must already be on the session list, with the sourced
script (default sample_10mb.R) in WORKING_DIR; nothing is created.
"""
import pytest

from common.config import env, env_list
from common.helper_function import (
    capture_server_and_db_metrics,
    describe_script_runs,
    failed,
    is_headless,
    login_and_snapshot,
    mean,
    optional_int,
    otel_sample,
    otel_samples,
    print_results,
)
from common.otel_metrics import capture_otel_metrics
from common.script_timings import (
    run_timed_console_command,
    script_timings_csv_path,
    write_script_timings_csv,
)
from common.session_actions import open_existing_session, set_session_working_directory
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_concurrent import concurrent_source_script

pytestmark = pytest.mark.rstudio_local

SESSION_NAME = env("RSTUDIO_SCRIPT_SESSION_NAME", "AUTO_SESSION_1")
SOURCE_COMMAND = env("RSTUDIO_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
SCRIPT_TIMEOUT_MS = int(env("RSTUDIO_SCRIPT_TIMEOUT_MS", "300000"))
METRICS_WINDOW_MINUTES = int(env("RSTUDIO_METRICS_WINDOW_MINUTES", "5"))
WORKING_DIR = env("RSTUDIO_SCRIPT_WORKING_DIR", "/home/posit")

# Concurrent variant: RSTUDIO_SCRIPT_SESSION_NAMES (comma-separated) overrides
# the default AUTO_SESSION_1..RSTUDIO_CONCURRENT_USERS list.
CONCURRENT_USERS = int(env("RSTUDIO_CONCURRENT_USERS", "3"))
CONCURRENT_SESSION_NAMES = env_list(
    "RSTUDIO_SCRIPT_SESSION_NAMES",
    ["AUTO_SESSION_%d" % (i + 1) for i in range(CONCURRENT_USERS)],
)
REQUESTS_PER_USER = int(env("RSTUDIO_REQUESTS_PER_USER", "1"))
THINK_TIME_SECONDS = float(env("RSTUDIO_THINK_TIME_SECONDS", "0"))
CONCURRENT_MAX_WORKERS = env("RSTUDIO_CONCURRENT_MAX_WORKERS")


def test_existing_session_source_large_script_time(page):
    """SESSION_NAME sources SOURCE_COMMAND once; the run is timed.
    Writes evidence/rstudio_script_timings_source_large_script.csv.

    Flow: login -> open existing session -> setwd -> run script -> capture
    metrics -> quit any session the run created
    """
    home_url, before_ids = login_and_snapshot(page)
    script_runs = []

    try:
        launch = open_existing_session(page, home_url, SESSION_NAME)
        set_session_working_directory(page, path=WORKING_DIR)

        script_elapsed_s = run_timed_console_command(
            page, SESSION_NAME, SOURCE_COMMAND, SCRIPT_TIMEOUT_MS, script_runs
        )

        print(
            "\n[rstudio-local] %s: launch=%.2fs, %r took %.2fs"
            % (SESSION_NAME, launch.elapsed_s, SOURCE_COMMAND, script_elapsed_s)
        )
        assert script_elapsed_s > 0

        capture_server_and_db_metrics(METRICS_WINDOW_MINUTES)
        capture_otel_metrics(
            [otel_sample(1, script_elapsed_s, launch.bytes_received)],
            run_elapsed=launch.elapsed_s + script_elapsed_s,
            scenario="source_large_script",
        )
    finally:
        write_script_timings_csv(script_timings_csv_path("source_large_script"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def test_existing_sessions_source_script_concurrently_time(page, request):
    """One user per CONCURRENT_SESSION_NAMES entry, each in their own browser,
    sources SOURCE_COMMAND REQUESTS_PER_USER times at the same time, pausing
    THINK_TIME_SECONDS between runs.
    Writes evidence/rstudio_script_timings_source_script_concurrent_users.csv.

    Flow: login -> per session in parallel: open browser -> login -> open
    existing session -> setwd -> run script -> capture metrics -> quit any
    session the run created
    """
    home_url, before_ids = login_and_snapshot(page)
    script_runs = []

    try:
        results, run_elapsed = concurrent_source_script(
            CONCURRENT_SESSION_NAMES,
            source_command=SOURCE_COMMAND,
            timeout_ms=SCRIPT_TIMEOUT_MS,
            working_dir=WORKING_DIR,
            think_time_s=THINK_TIME_SECONDS,
            iterations=REQUESTS_PER_USER,
            max_workers=optional_int(CONCURRENT_MAX_WORKERS),
            headless=is_headless(request),
            script_runs=script_runs,
        )
        print_results(results, len(CONCURRENT_SESSION_NAMES), "user", describe_script_runs(SOURCE_COMMAND))

        failures = failed(results)
        assert not failures, "one or more concurrent users failed: %s" % failures

        capture_server_and_db_metrics(METRICS_WINDOW_MINUTES)
        capture_otel_metrics(
            otel_samples(results, lambda r: mean(r["run_elapsed_s"])),
            run_elapsed=run_elapsed,
            scenario="source_script_concurrent_users",
        )
    finally:
        write_script_timings_csv(script_timings_csv_path("source_script_concurrent_users"), script_runs)
        cleanup_and_verify_sessions(page, home_url, before_ids)
