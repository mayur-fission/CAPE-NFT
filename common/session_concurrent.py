"""Run sessions truly in parallel to simulate several users at once. Each
worker drives its own isolated Playwright browser (the sync API can't be
shared across threads), and logins are staggered so they don't collide.

Workers retry their whole flow up to LAUNCH_ATTEMPTS times, reusing their
reserved session name. Callers diff row ids and clean up afterwards.
"""
import concurrent.futures
import time
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from common.config import default_session_count, env, max_workers_for
from common.rstudio_session_helper import next_auto_perf_session_names
from common.rstudio_text_operations import create_text_file
from common.script_timings import run_timed_console_command_repeatedly
from common.session_actions import (
    launch_session,
    login_to_posit_workbench,
    open_existing_session,
    set_session_working_directory,
)
from common.session_retry import result_with_retries, stagger_login
from common.session_scenarios import (
    default_run_script_command,
    default_run_script_timeout_ms,
    launch_with_project_and_text_file,
    launch_with_project_and_text_files,
    next_run_r_session_names,
)


@contextmanager
def _isolated_session_list(headless=True):
    """Yield (page, home_url): a fresh browser of its own, logged in (after
    stagger_login()) and showing the session list. Closed on exit."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--start-maximized"])
        try:
            context = browser.new_context(ignore_https_errors=True, no_viewport=not headless)
            page = context.new_page()
            stagger_login()
            yield page, login_to_posit_workbench(page)
        finally:
            browser.close()


def _run_in_parallel(session_names, worker, workers):
    """worker(index, session_name) for every name on `workers` threads, each
    wrapped in result_with_retries(). Returns (results in index order,
    run_elapsed_s)."""
    run_started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                result_with_retries, i + 1, name,
                lambda _attempt, i=i, name=name: worker(i + 1, name),
                "user %d (%s)" % (i + 1, name),
            )
            for i, name in enumerate(session_names)
        ]
        results = [f.result() for f in futures]
    return sorted(results, key=lambda r: r["index"]), time.time() - run_started


def concurrent_launch_sessions(page, count=None, max_workers=None):
    """Launch `count` new sessions (default RSTUDIO_SESSION_COUNT) in
    parallel, each setting its working directory. `page` must be logged in;
    it only reserves the names. max_workers defaults to
    RSTUDIO_CONCURRENT_MAX_WORKERS, else one per session.

    Returns (results, run_elapsed_s); results carry "elapsed_s" and
    "bytes_received" on success.
    """
    count = count or default_session_count()

    def _worker(index, session_name):
        with _isolated_session_list() as (worker_page, home_url):
            launch = launch_session(worker_page, home_url, session_name=session_name)
            set_session_working_directory(worker_page)
            return {"elapsed_s": launch.elapsed_s, "bytes_received": launch.bytes_received}

    return _run_in_parallel(next_auto_perf_session_names(page, count), _worker, max_workers_for(count, max_workers))


def concurrent_launch_with_project_and_text_file(
    page, count=None, max_workers=None, working_dir=None, content=None, folder=None, file_name=None,
    headless=True,
):
    """launch_with_project_and_text_file() for `count` users (default
    RSTUDIO_SESSION_COUNT) in parallel. Names are reserved up front from
    `page` (once a session has a project its row shows the project name, so
    names can't be scanned later).

    Returns (results, run_elapsed_s); results carry "launch_elapsed_s",
    "project_elapsed_s", "text_file_elapsed_s" and "bytes_received".
    """
    count = count or default_session_count()

    def _worker(index, session_name):
        with _isolated_session_list(headless) as (worker_page, home_url):
            outcome = launch_with_project_and_text_file(
                worker_page, home_url, session_name=session_name,
                working_dir=working_dir, content=content, folder=folder, file_name=file_name,
            )
            return {
                "launch_elapsed_s": outcome.launch.elapsed_s,
                "project_elapsed_s": outcome.project.elapsed_s,
                "text_file_elapsed_s": outcome.text_file.elapsed_s,
                "bytes_received": outcome.launch.bytes_received,
            }

    return _run_in_parallel(next_auto_perf_session_names(page, count), _worker, max_workers_for(count, max_workers))


def concurrent_launch_with_project_and_text_files(
    page, count=None, max_workers=None, working_dir=None, content=None, folder=None, file_count=1,
    headless=True,
):
    """concurrent_launch_with_project_and_text_file(), but each user creates
    `file_count` text files; "text_file_elapsed_s" is a list per user."""
    count = count or default_session_count()

    def _worker(index, session_name):
        with _isolated_session_list(headless) as (worker_page, home_url):
            outcome = launch_with_project_and_text_files(
                worker_page, home_url, session_name=session_name,
                working_dir=working_dir, content=content, folder=folder, file_count=file_count,
            )
            return {
                "launch_elapsed_s": outcome.launch.elapsed_s,
                "project_elapsed_s": outcome.project.elapsed_s,
                "text_file_elapsed_s": [tf.elapsed_s for tf in outcome.text_files],
                "bytes_received": outcome.launch.bytes_received,
            }

    return _run_in_parallel(next_auto_perf_session_names(page, count), _worker, max_workers_for(count, max_workers))


def concurrent_source_script(
    session_names, source_command=None, timeout_ms=None, working_dir=None,
    think_time_s=0.0, iterations=1, max_workers=None, headless=True, script_runs=None,
):
    """One user per existing session in `session_names`, in parallel: open
    it, setwd to `working_dir` (default RSTUDIO_WORKING_DIR) and run
    source_command (default RSTUDIO_SCRIPT_SOURCE_COMMAND) `iterations`
    times, pausing think_time_s between runs. Every run, retries included,
    is recorded in `script_runs`.

    Returns (results, run_elapsed_s); results carry "launch_elapsed_s",
    "run_elapsed_s" (one per run) and "bytes_received".
    """
    source_command = source_command or env("RSTUDIO_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
    timeout_ms = timeout_ms or int(env("RSTUDIO_SCRIPT_TIMEOUT_MS", "300000"))

    def _worker(index, session_name):
        with _isolated_session_list(headless) as (worker_page, home_url):
            launch = open_existing_session(worker_page, home_url, session_name)
            set_session_working_directory(worker_page, path=working_dir)
            run_elapsed_s = run_timed_console_command_repeatedly(
                worker_page, session_name, source_command, timeout_ms, iterations, think_time_s, script_runs
            )
            return {
                "launch_elapsed_s": launch.elapsed_s,
                "run_elapsed_s": run_elapsed_s,
                "bytes_received": launch.bytes_received,
            }

    return _run_in_parallel(session_names, _worker, max_workers_for(len(session_names), max_workers))


def concurrent_create_text_files(
    session_names, file_count=1, working_dir=None, content=None, folder=None, max_workers=None, headless=True,
):
    """One user per existing session in `session_names`, in parallel: open
    it and create `file_count` text files, timed apart from the open. The
    sessions must already have a project (e.g. from
    create_multiple_sessions_with_projects()); working_dir, if given, is
    re-set after opening.

    Returns (results, run_elapsed_s); results carry "open_elapsed_s",
    "text_file_elapsed_s" (one per file) and "bytes_received".
    """
    def _worker(index, session_name):
        with _isolated_session_list(headless) as (worker_page, home_url):
            launch = open_existing_session(worker_page, home_url, session_name)
            if working_dir:
                set_session_working_directory(worker_page, path=working_dir)
            return {
                "open_elapsed_s": launch.elapsed_s,
                "text_file_elapsed_s": [
                    create_text_file(worker_page, content=content, folder=folder).elapsed_s
                    for _ in range(file_count)
                ],
                "bytes_received": launch.bytes_received,
            }

    return _run_in_parallel(session_names, _worker, max_workers_for(len(session_names), max_workers))


def concurrent_launch_and_run_script(
    page, count=None, source_command=None, timeout_ms=None, working_dir=None,
    think_time_s=0.0, run_count=1, max_workers=None, headless=True, script_runs=None,
):
    """launch_and_run_script() for `count` new Run_R_session_<N> sessions
    (default RSTUDIO_RUN_SCRIPT_CONCURRENT_USERS) in parallel. `page` must be
    logged in; it only reserves the names. max_workers defaults to
    RSTUDIO_RUN_SCRIPT_CONCURRENT_MAX_WORKERS. Every run, retries included,
    is recorded in `script_runs`.

    Returns (results, run_elapsed_s); results carry "launch_elapsed_s",
    "run_elapsed_s" (one per run) and "bytes_received".
    """
    count = count or int(env("RSTUDIO_RUN_SCRIPT_CONCURRENT_USERS", "3"))
    source_command = source_command or default_run_script_command()
    timeout_ms = timeout_ms or default_run_script_timeout_ms()

    def _worker(index, session_name):
        with _isolated_session_list(headless) as (worker_page, home_url):
            launch = launch_session(worker_page, home_url, session_name=session_name)
            set_session_working_directory(worker_page, path=working_dir)
            run_elapsed_s = run_timed_console_command_repeatedly(
                worker_page, session_name, source_command, timeout_ms, run_count, think_time_s, script_runs
            )
            return {
                "launch_elapsed_s": launch.elapsed_s,
                "run_elapsed_s": run_elapsed_s,
                "bytes_received": launch.bytes_received,
            }

    return _run_in_parallel(
        next_run_r_session_names(page, count), _worker,
        max_workers_for(count, max_workers, "RSTUDIO_RUN_SCRIPT_CONCURRENT_MAX_WORKERS"),
    )
