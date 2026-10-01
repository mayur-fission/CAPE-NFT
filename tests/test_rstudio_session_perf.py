"""RStudio Pro session performance: launch timing (single / sequential /
concurrent) and launch + project + text-file scenarios, on a local Workbench.
    pytest tests/test_rstudio_session_perf.py -v -m rstudio_local
"""
import pytest

from common.config import env
from common.helper_function import (
    close_tabs,
    describe_launch_project,
    describe_launch_project_text_file,
    describe_launch_project_text_files,
    describe_open_text_files,
    failed,
    is_headless,
    optional_float,
    optional_int,
    otel_sample,
    otel_samples,
    print_results,
)
from common.otel_metrics import capture_otel_metrics
from common.perf_scenarios import (
    run_launch_perf_concurrent,
    run_launch_perf_multiple,
    run_launch_perf_single,
)
from common.session_actions import login_to_posit_workbench
from common.session_batch import create_multiple_sessions_with_projects
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_concurrent import (
    concurrent_create_text_files,
    concurrent_launch_with_project_and_text_files,
    concurrent_launch_with_project_and_text_file,
)
from common.session_scenarios import (
    launch_with_project_and_text_file,
    launch_in_tabs_with_project_and_text_file,
)
from common.rstudio_session_helper import row_ids

pytestmark = pytest.mark.rstudio_local

# Sessions for the multiple/concurrent tests, and text files per user.
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))
TEXT_FILE_COUNT = int(env("RSTUDIO_TEXT_FILE_COUNT", "3"))
P95_MAX_SECONDS = env("RSTUDIO_LAUNCH_P95_MAX_SECONDS")
CONCURRENT_MAX_WORKERS = env("RSTUDIO_CONCURRENT_MAX_WORKERS")

@pytest.mark.skip()
def test_rstudio_session_launch_performance(page):
    run_launch_perf_single(page, p95_max_seconds=optional_float(P95_MAX_SECONDS))


def test_rstudio_multiple_sessions_launch_performance(page):
    """Launches SESSION_COUNT sessions one after another on one page."""
    run_launch_perf_multiple(
        page, count=SESSION_COUNT, p95_max_seconds=optional_float(P95_MAX_SECONDS),
    )

@pytest.mark.skip()
def test_rstudio_concurrent_sessions_launch_performance(page):
    """Launches SESSION_COUNT sessions at once, one isolated browser each
    (capped by RSTUDIO_CONCURRENT_MAX_WORKERS)."""
    run_launch_perf_concurrent(
        page,
        count=SESSION_COUNT,
        max_workers=optional_int(CONCURRENT_MAX_WORKERS),
        p95_max_seconds=optional_float(P95_MAX_SECONDS),
    )

@pytest.mark.skip()
def test_rstudio_single_user_session_project_and_text_file(page):
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)

    try:
        result = launch_with_project_and_text_file(page, home_url)

        print(
            "\n[rstudio-local] single-user session+project+text-file: "
            "launch=%.2fs project=%.2fs (wd=%s) text_file=%.2fs (saved %s/%s)"
            % (
                result.launch.elapsed_s,
                result.project.elapsed_s,
                result.project.working_directory,
                result.text_file.elapsed_s,
                result.text_file.folder,
                result.text_file.file_name,
            )
        )

        assert result.text_file.file_name, "create_text_file() returned no file_name"
        assert result.text_file.folder, "create_text_file() returned no folder"

        capture_otel_metrics(
            [
                otel_sample(1, result.launch.elapsed_s, result.launch.bytes_received),
                otel_sample(2, result.project.elapsed_s),
                otel_sample(3, result.text_file.elapsed_s),
            ],
            run_elapsed=result.launch.elapsed_s + result.project.elapsed_s + result.text_file.elapsed_s,
            scenario="single_user_project_and_text_file",
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)

@pytest.mark.skip()
def test_rstudio_multiple_sessions_project_and_text_file_in_tabs(page, context):
    """Launch + project + text file, repeated SESSION_COUNT times, each
    session in its own tab on the shared login."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)

    tabs = []
    try:
        results, tabs, run_elapsed = launch_in_tabs_with_project_and_text_file(
            context, home_url, count=SESSION_COUNT
        )
        print_results(results, SESSION_COUNT, "tab", describe_launch_project_text_file)

        failures = failed(results)
        assert not failures, "one or more tabs failed: %s" % failures

        capture_otel_metrics(
            otel_samples(results, lambda r: r["launch_elapsed_s"] + r["project_elapsed_s"] + r["text_file_elapsed_s"]),
            run_elapsed=run_elapsed,
            scenario="multiple_sessions_project_and_text_file_in_tabs",
        )
    finally:
        close_tabs(tabs)
        cleanup_and_verify_sessions(page, home_url, before_ids)

@pytest.mark.skip()
def test_rstudio_concurrent_users_project_and_text_file(page, request):
    """Launch + project + text file by SESSION_COUNT users at once, each in
    its own isolated browser."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)

    try:
        results, run_elapsed = concurrent_launch_with_project_and_text_file(
            page, count=SESSION_COUNT, headless=is_headless(request)
        )
        print_results(results, SESSION_COUNT, "user", describe_launch_project_text_file)

        failures = failed(results)
        assert not failures, "one or more users failed: %s" % failures

        capture_otel_metrics(
            otel_samples(results, lambda r: r["launch_elapsed_s"] + r["project_elapsed_s"] + r["text_file_elapsed_s"]),
            run_elapsed=run_elapsed,
            scenario="concurrent_users_project_and_text_file",
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)

@pytest.mark.skip()
def test_rstudio_concurrent_users_project_and_multiple_text_files(page, request):
    """Same as the concurrent single-file test, but each user creates
    TEXT_FILE_COUNT text files."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)

    try:
        results, run_elapsed = concurrent_launch_with_project_and_text_files(
            page, count=SESSION_COUNT, file_count=TEXT_FILE_COUNT, headless=is_headless(request)
        )
        print_results(results, SESSION_COUNT, "user", describe_launch_project_text_files)

        failures = failed(results)
        assert not failures, "one or more users failed: %s" % failures

        capture_otel_metrics(
            otel_samples(results, lambda r: r["launch_elapsed_s"] + r["project_elapsed_s"] + sum(r["text_file_elapsed_s"])),
            run_elapsed=run_elapsed,
            scenario="concurrent_users_project_and_multiple_text_files",
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)

@pytest.mark.skip()
def test_rstudio_multiple_sessions_then_concurrent_text_files(page, request):
    """Phase 1: SESSION_COUNT sessions with projects, launched sequentially.
    Phase 2: every session reopened in its own browser, TEXT_FILE_COUNT text
    files written in all at once - so launch time is excluded from the
    concurrent file-writing being timed."""
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)

    try:
        launch_results, launch_run_elapsed = create_multiple_sessions_with_projects(
            page, home_url, count=SESSION_COUNT
        )
        print_results(launch_results, SESSION_COUNT, "phase1 session", describe_launch_project)

        launch_failures = failed(launch_results)
        assert not launch_failures, "one or more sessions failed to launch: %s" % launch_failures

        capture_otel_metrics(
            otel_samples(launch_results, lambda r: r["launch_elapsed_s"] + r["project_elapsed_s"]),
            run_elapsed=launch_run_elapsed,
            scenario="multiple_sessions_with_projects",
        )

        session_names = [r["session_name"] for r in launch_results]
        files_results, files_run_elapsed = concurrent_create_text_files(
            session_names, file_count=TEXT_FILE_COUNT, headless=is_headless(request)
        )
        print_results(files_results, SESSION_COUNT, "phase2 session", describe_open_text_files)

        files_failures = failed(files_results)
        assert not files_failures, "one or more sessions failed to write files: %s" % files_failures

        capture_otel_metrics(
            otel_samples(files_results, lambda r: r["open_elapsed_s"] + sum(r["text_file_elapsed_s"])),
            run_elapsed=files_run_elapsed,
            scenario="concurrent_text_files_in_existing_sessions",
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)
