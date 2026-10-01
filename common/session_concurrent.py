"""Run sessions truly in parallel, each worker in its own isolated
Playwright browser (the sync API can't be shared across threads).
Simulates several users at once; logins are staggered so they don't
collide.

Workers retry the whole flow up to _LAUNCH_ATTEMPTS times, reusing their
reserved session name. Callers diff row ids and clean up afterwards.
"""
import concurrent.futures
import time

from playwright.sync_api import sync_playwright

from common.config import env
from common.rstudio_text_operations import create_text_file as _create_text_file
from common.rstudio_session_helper import next_auto_perf_session_names as _next_auto_perf_session_names

from common.script_timings import run_timed_console_command
from common.session_actions import login_to_posit_workbench
from common.session_actions import launch_session
from common.session_actions import open_existing_session
from common.session_actions import set_session_working_directory
from common.session_scenarios import launch_with_project_and_text_file
from common.session_scenarios import launch_with_project_and_text_files
from common.session_scenarios import next_run_r_session_names
from common.session_retry import _LAUNCH_ATTEMPTS
from common.session_retry import _retry_backoff_s
from common.session_retry import _stagger_login


def _launch_session_isolated(index, session_name):
    """Worker for concurrent_launch_sessions(): log in, launch `session_name`
    and set its working directory in a headless browser.
    """
    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    _stagger_login()
                    home_url = login_to_posit_workbench(page)
                    launch = launch_session(page, home_url, session_name=session_name)
                    set_session_working_directory(page)
                    if attempt:
                        print("[rstudio-local] session %d (%s) launched on attempt %d/%d"
                              % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS))
                    return {
                        "index": index,
                        "ok": True,
                        "elapsed_s": launch.elapsed_s,
                        "bytes_received": launch.bytes_received,
                        "session_name": launch.session_name,
                        "attempts": attempt + 1,
                    }
                finally:
                    browser.close()
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] session %d (%s) attempt %d/%d failed: %s"
                  % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def concurrent_launch_sessions(page, count=None, max_workers=None):
    """Launch `count` sessions (default RSTUDIO_SESSION_COUNT) in parallel.

    `page` must be logged in; it's only used to reserve names up front.
    max_workers defaults to RSTUDIO_CONCURRENT_MAX_WORKERS, else `count`.

    Returns (results, run_elapsed_s), results in index order.
    """
    if count is None:
        count = int(env("RSTUDIO_SESSION_COUNT", "10"))
    env_max_workers = env("RSTUDIO_CONCURRENT_MAX_WORKERS")
    workers = max(1, max_workers or (int(env_max_workers) if env_max_workers else None) or count)

    session_names = _next_auto_perf_session_names(page, count)

    run_started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_launch_session_isolated, i + 1, session_names[i])
            for i in range(count)
        ]
        results = [f.result() for f in futures]
    run_elapsed = time.time() - run_started

    results.sort(key=lambda r: r["index"])
    return results, run_elapsed


def _launch_with_project_and_text_file_isolated(
    index, session_name, working_dir=None, content=None, folder=None, file_name=None, headless=True
):
    """Worker for concurrent_launch_with_project_and_text_file(): run
    launch_with_project_and_text_file() in its own browser.
    """
    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    _stagger_login()
                    home_url = login_to_posit_workbench(page)
                    outcome = launch_with_project_and_text_file(
                        page, home_url, session_name=session_name,
                        working_dir=working_dir, content=content, folder=folder, file_name=file_name,
                    )
                    if attempt:
                        print("[rstudio-local] user %d (%s) completed on attempt %d/%d"
                              % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS))
                    return {
                        "index": index,
                        "ok": True,
                        "session_name": session_name,
                        "launch_elapsed_s": outcome.launch.elapsed_s,
                        "project_elapsed_s": outcome.project.elapsed_s,
                        "text_file_elapsed_s": outcome.text_file.elapsed_s,
                        "bytes_received": outcome.launch.bytes_received,
                        "attempts": attempt + 1,
                    }
                finally:
                    browser.close()
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] user %d (%s) attempt %d/%d failed: %s"
                  % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def concurrent_launch_with_project_and_text_file(
    page, count=None, max_workers=None, working_dir=None, content=None, folder=None, file_name=None,
    headless=True,
):
    """launch_with_project_and_text_file() for `count` users in parallel.

    Names are reserved up front from `page` (a session's row shows its
    project name once one exists, so names can't be scanned later). Other
    options pass through to each worker.

    Returns (results, run_elapsed_s), results in index order.
    """
    if count is None:
        count = int(env("RSTUDIO_SESSION_COUNT", "10"))
    env_max_workers = env("RSTUDIO_CONCURRENT_MAX_WORKERS")
    workers = max(1, max_workers or (int(env_max_workers) if env_max_workers else None) or count)

    session_names = _next_auto_perf_session_names(page, count)

    run_started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _launch_with_project_and_text_file_isolated, i + 1, session_names[i],
                working_dir, content, folder, file_name, headless,
            )
            for i in range(count)
        ]
        results = [f.result() for f in futures]
    run_elapsed = time.time() - run_started

    results.sort(key=lambda r: r["index"])
    return results, run_elapsed


def _launch_with_project_and_text_files_isolated(
    index, session_name, working_dir=None, content=None, folder=None, file_count=1, headless=True
):
    """Worker for concurrent_launch_with_project_and_text_files(): run
    launch_with_project_and_text_files() in its own browser.
    """
    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    _stagger_login()
                    home_url = login_to_posit_workbench(page)
                    outcome = launch_with_project_and_text_files(
                        page, home_url, session_name=session_name,
                        working_dir=working_dir, content=content, folder=folder, file_count=file_count,
                    )
                    if attempt:
                        print("[rstudio-local] user %d (%s) completed on attempt %d/%d"
                              % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS))
                    return {
                        "index": index,
                        "ok": True,
                        "session_name": session_name,
                        "launch_elapsed_s": outcome.launch.elapsed_s,
                        "project_elapsed_s": outcome.project.elapsed_s,
                        "text_file_elapsed_s": [tf.elapsed_s for tf in outcome.text_files],
                        "bytes_received": outcome.launch.bytes_received,
                        "attempts": attempt + 1,
                    }
                finally:
                    browser.close()
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] user %d (%s) attempt %d/%d failed: %s"
                  % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def concurrent_launch_with_project_and_text_files(
    page, count=None, max_workers=None, working_dir=None, content=None, folder=None, file_count=1,
    headless=True,
):
    """concurrent_launch_with_project_and_text_file(), but each user creates
    `file_count` text files.

    Returns (results, run_elapsed_s); text_file_elapsed_s is a list per user.
    """
    if count is None:
        count = int(env("RSTUDIO_SESSION_COUNT", "10"))
    env_max_workers = env("RSTUDIO_CONCURRENT_MAX_WORKERS")
    workers = max(1, max_workers or (int(env_max_workers) if env_max_workers else None) or count)

    session_names = _next_auto_perf_session_names(page, count)

    run_started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _launch_with_project_and_text_files_isolated, i + 1, session_names[i],
                working_dir, content, folder, file_count, headless,
            )
            for i in range(count)
        ]
        results = [f.result() for f in futures]
    run_elapsed = time.time() - run_started

    results.sort(key=lambda r: r["index"])
    return results, run_elapsed


def _source_script_isolated(
    index, session_name, source_command, timeout_ms=300000, working_dir=None,
    think_time_s=0.0, iterations=1, headless=True, script_runs=None,
):
    """Worker for concurrent_source_script(): open the existing session
    `session_name` and run source_command `iterations` times, sleeping
    think_time_s between runs.
    """
    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    _stagger_login()
                    home_url = login_to_posit_workbench(page)
                    launch = open_existing_session(page, home_url, session_name)
                    set_session_working_directory(page, path=working_dir)

                    run_elapsed_s = []
                    for i in range(iterations):
                        run_elapsed_s.append(
                            run_timed_console_command(
                                page, session_name, source_command, timeout_ms, script_runs
                            )
                        )
                        if think_time_s and i + 1 < iterations:
                            time.sleep(think_time_s)

                    if attempt:
                        print("[rstudio-local] user %d (%s) completed on attempt %d/%d"
                              % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS))
                    return {
                        "index": index,
                        "ok": True,
                        "session_name": session_name,
                        "launch_elapsed_s": launch.elapsed_s,
                        "run_elapsed_s": run_elapsed_s,
                        "bytes_received": launch.bytes_received,
                        "attempts": attempt + 1,
                    }
                finally:
                    browser.close()
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] user %d (%s) attempt %d/%d failed: %s"
                  % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def concurrent_source_script(
    session_names, source_command=None, timeout_ms=None, working_dir=None,
    think_time_s=0.0, iterations=1, max_workers=None, headless=True, script_runs=None,
):
    """Run source_command in each existing session in `session_names` in
    parallel, one user per session.

    Sessions must already exist. Defaults come from RSTUDIO_SCRIPT_* and
    RSTUDIO_WORKING_DIR. Every run (retries included) is recorded in
    `script_runs` (see common/script_timings.py). Returns (results,
    run_elapsed_s).
    """
    source_command = source_command or env("RSTUDIO_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
    timeout_ms = timeout_ms or int(env("RSTUDIO_SCRIPT_TIMEOUT_MS", "300000"))
    env_max_workers = env("RSTUDIO_CONCURRENT_MAX_WORKERS")
    workers = max(1, max_workers or (int(env_max_workers) if env_max_workers else None) or len(session_names))

    run_started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _source_script_isolated,
                i + 1, session_names[i], source_command, timeout_ms, working_dir,
                think_time_s, iterations, headless, script_runs,
            )
            for i in range(len(session_names))
        ]
        results = [f.result() for f in futures]
    run_elapsed = time.time() - run_started

    results.sort(key=lambda r: r["index"])
    return results, run_elapsed


def _create_text_files_isolated(
    index, session_name, file_count=1, working_dir=None, content=None, folder=None, headless=True
):
    """Worker for concurrent_create_text_files(): open the existing session
    `session_name` and create `file_count` text files in it.
    """
    kwargs = {}
    if content is not None:
        kwargs["content"] = content
    if folder is not None:
        kwargs["folder"] = folder

    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    _stagger_login()
                    home_url = login_to_posit_workbench(page)
                    launch = open_existing_session(page, home_url, session_name)
                    if working_dir:
                        set_session_working_directory(page, path=working_dir)

                    text_file_elapsed_s = [
                        _create_text_file(page, **kwargs).elapsed_s for _ in range(file_count)
                    ]

                    if attempt:
                        print("[rstudio-local] session %s completed on attempt %d/%d"
                              % (session_name, attempt + 1, _LAUNCH_ATTEMPTS))
                    return {
                        "index": index,
                        "ok": True,
                        "session_name": session_name,
                        "open_elapsed_s": launch.elapsed_s,
                        "text_file_elapsed_s": text_file_elapsed_s,
                        "bytes_received": launch.bytes_received,
                        "attempts": attempt + 1,
                    }
                finally:
                    browser.close()
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] session %s attempt %d/%d failed: %s"
                  % (session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def concurrent_create_text_files(
    session_names, file_count=1, working_dir=None, content=None, folder=None, max_workers=None, headless=True,
):
    """Create `file_count` text files in each existing session in
    `session_names`, in parallel.

    Times file writes separately from session launch. Sessions must already
    have a project (e.g. from create_multiple_sessions_with_projects()).
    working_dir, if given, is re-set after opening. Returns (results,
    run_elapsed_s).
    """
    env_max_workers = env("RSTUDIO_CONCURRENT_MAX_WORKERS")
    workers = max(1, max_workers or (int(env_max_workers) if env_max_workers else None) or len(session_names))

    run_started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _create_text_files_isolated,
                i + 1, session_names[i], file_count, working_dir, content, folder, headless,
            )
            for i in range(len(session_names))
        ]
        results = [f.result() for f in futures]
    run_elapsed = time.time() - run_started

    results.sort(key=lambda r: r["index"])
    return results, run_elapsed


def _launch_and_run_script_isolated(
    index, session_name, source_command, timeout_ms=300000, working_dir=None,
    think_time_s=0.0, run_count=1, headless=True, script_runs=None,
):
    """Worker for concurrent_launch_and_run_script(): launch `session_name`,
    set its working directory and run source_command `run_count` times.
    """
    last_exc = None
    for attempt in range(_LAUNCH_ATTEMPTS):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                try:
                    context = browser.new_context(ignore_https_errors=True)
                    page = context.new_page()
                    _stagger_login()
                    home_url = login_to_posit_workbench(page)
                    launch = launch_session(page, home_url, session_name=session_name)
                    set_session_working_directory(page, path=working_dir)

                    run_elapsed_s = []
                    for i in range(run_count):
                        run_elapsed_s.append(
                            run_timed_console_command(
                                page, session_name, source_command, timeout_ms, script_runs
                            )
                        )
                        if think_time_s and i + 1 < run_count:
                            time.sleep(think_time_s)

                    if attempt:
                        print("[rstudio-local] user %d (%s) completed on attempt %d/%d"
                              % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS))
                    return {
                        "index": index,
                        "ok": True,
                        "session_name": session_name,
                        "launch_elapsed_s": launch.elapsed_s,
                        "run_elapsed_s": run_elapsed_s,
                        "bytes_received": launch.bytes_received,
                        "attempts": attempt + 1,
                    }
                finally:
                    browser.close()
        except Exception as exc:
            last_exc = exc
            print("[rstudio-local] user %d (%s) attempt %d/%d failed: %s"
                  % (index, session_name, attempt + 1, _LAUNCH_ATTEMPTS, exc))
            if attempt + 1 < _LAUNCH_ATTEMPTS:
                time.sleep(_retry_backoff_s(attempt))

    return {
        "index": index,
        "ok": False,
        "error": str(last_exc),
        "session_name": session_name,
        "attempts": _LAUNCH_ATTEMPTS,
    }


def concurrent_launch_and_run_script(
    page, count=None, source_command=None, timeout_ms=None, working_dir=None,
    think_time_s=0.0, run_count=1, max_workers=None, headless=True, script_runs=None,
):
    """launch_and_run_script() for `count` new Run_R_session_<N> sessions in
    parallel.

    `page` must be logged in; it's only used to reserve names. count defaults
    to RSTUDIO_RUN_SCRIPT_CONCURRENT_USERS and max_workers to
    RSTUDIO_RUN_SCRIPT_CONCURRENT_MAX_WORKERS. Every run (retries included)
    is recorded in `script_runs` (see common/script_timings.py). Returns
    (results, run_elapsed_s).
    """
    if count is None:
        count = int(env("RSTUDIO_RUN_SCRIPT_CONCURRENT_USERS", "3"))
    source_command = source_command or env("RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
    timeout_ms = timeout_ms or int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))
    env_max_workers = env("RSTUDIO_RUN_SCRIPT_CONCURRENT_MAX_WORKERS")
    workers = max(1, max_workers or (int(env_max_workers) if env_max_workers else None) or count)

    session_names = next_run_r_session_names(page, count)

    run_started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _launch_and_run_script_isolated,
                i + 1, session_names[i], source_command, timeout_ms, working_dir,
                think_time_s, run_count, headless, script_runs,
            )
            for i in range(count)
        ]
        results = [f.result() for f in futures]
    run_elapsed = time.time() - run_started

    results.sort(key=lambda r: r["index"])
    return results, run_elapsed
