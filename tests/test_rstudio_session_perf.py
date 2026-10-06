"""RStudio Pro session performance on a local Workbench: launch time (single /
sequential / concurrent) and launch + project + text-file scenarios. Every
test force-quits the sessions it created.

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
    login_and_snapshot,
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
from common.session_batch import create_multiple_sessions_with_projects
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_concurrent import (
    concurrent_create_text_files,
    concurrent_launch_with_project_and_text_file,
    concurrent_launch_with_project_and_text_files,
)
from common.session_scenarios import (
    launch_in_tabs_with_project_and_text_file,
    launch_with_project_and_text_file,
)

pytestmark = pytest.mark.rstudio_local

# Sessions for the multiple/concurrent tests, and text files per user.
SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))
TEXT_FILE_COUNT = int(env("RSTUDIO_TEXT_FILE_COUNT", "3"))
P95_MAX_SECONDS = env("RSTUDIO_LAUNCH_P95_MAX_SECONDS")
CONCURRENT_MAX_WORKERS = env("RSTUDIO_CONCURRENT_MAX_WORKERS")


@pytest.mark.skip()
def test_single_session_launch_time(page):
    """One session's launch time, written to the launch-perf reports; fails if
    p95 exceeds RSTUDIO_LAUNCH_P95_MAX_SECONDS (when set).

    Flow: login -> launch new session -> write reports -> quit session
    """
    run_launch_perf_single(page, p95_max_seconds=optional_float(P95_MAX_SECONDS))


def test_sequential_sessions_launch_time(page):
    """SESSION_COUNT sessions launched one after another on one page; launch
    times go to the launch-perf reports. Fails if p95 exceeds
    RSTUDIO_LAUNCH_P95_MAX_SECONDS (when set).

    Flow: login -> launch sessions in turn -> write reports -> quit sessions
    """
    run_launch_perf_multiple(
        page, count=SESSION_COUNT, p95_max_seconds=optional_float(P95_MAX_SECONDS),
    )


@pytest.mark.skip()
def test_concurrent_sessions_launch_time(page):
    """SESSION_COUNT sessions launched at once, one isolated browser each
    (capped by RSTUDIO_CONCURRENT_MAX_WORKERS). Fails if p95 exceeds
    RSTUDIO_LAUNCH_P95_MAX_SECONDS (when set).

    Flow: login -> per user in parallel: open browser -> login -> launch new
    session -> write reports -> quit sessions
    """
    run_launch_perf_concurrent(
        page,
        count=SESSION_COUNT,
        max_workers=optional_int(CONCURRENT_MAX_WORKERS),
        p95_max_seconds=optional_float(P95_MAX_SECONDS),
    )


@pytest.mark.skip()
def test_single_session_create_project_and_text_file(page):
    """One user times each step of setting up a session for work.

    Flow: login -> launch new session -> create project -> create and save
    text file -> capture metrics -> quit session
    """
    home_url, before_ids = login_and_snapshot(page)

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
def test_sessions_in_tabs_create_project_and_text_file(page, context):
    """SESSION_COUNT sessions, each in its own tab of one login, are set up
    one after another.

    Flow: login -> per tab: launch new session -> create project -> create
    and save text file -> capture metrics -> close tabs -> quit sessions
    """
    home_url, before_ids = login_and_snapshot(page)

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
def test_concurrent_users_create_project_and_text_file(page, request):
    """SESSION_COUNT users, each in their own isolated browser, set up a
    session at the same time.

    Flow: login -> per user in parallel: open browser -> login -> launch new
    session -> create project -> create and save text file -> capture
    metrics -> quit sessions
    """
    home_url, before_ids = login_and_snapshot(page)

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
def test_concurrent_users_create_project_and_multiple_text_files(page, request):
    """Same as test_concurrent_users_create_project_and_text_file, but each
    user creates TEXT_FILE_COUNT text files.

    Flow: login -> per user in parallel: open browser -> login -> launch new
    session -> create project -> create and save TEXT_FILE_COUNT text files
    -> capture metrics -> quit sessions
    """
    home_url, before_ids = login_and_snapshot(page)

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
def test_sequential_sessions_then_concurrent_text_files(page, request):
    """Times concurrent file writing on its own, without launch time: sessions
    are created first, then all of them write files at once.

    Flow: login -> launch SESSION_COUNT sessions with projects in turn ->
    per session in parallel: open browser -> reopen session -> create and
    save TEXT_FILE_COUNT text files -> capture metrics -> quit sessions
    """
    home_url, before_ids = login_and_snapshot(page)

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
