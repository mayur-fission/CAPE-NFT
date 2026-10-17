"""Multi-step flows on one session at a time.

Flow: launch -> setwd -> create project (-> text file(s))
      launch -> setwd -> run R script (n times)
"""
import time
from collections import namedtuple

from common.perf_report import allure_step

from config.config import default_session_count, env, env_path
from common.rstudio_session_helper import goto_session_list, open_existing_session
from common.rstudio_text_operations import create_text_file
from common.rstudio_files import (
    create_project_from_existing_directory,
    set_working_directory,
    verify_working_directory,
)
from common.script_timings import run_timed_console_command_repeatedly
from common.session_actions import WORKING_DIR, launch_session, set_session_working_directory
from common.session_naming_logic import (
    RUN_R_SESSION_NAME_PREFIX,
    next_auto_perf_session_names,
    next_session_name,
)

SessionAndProject = namedtuple("SessionAndProject", ["launch", "project"])
SessionProjectAndTextFile = namedtuple("SessionProjectAndTextFile", ["launch", "project", "text_file"])
SessionProjectAndTextFiles = namedtuple("SessionProjectAndTextFiles", ["launch", "project", "text_files"])


def launch_with_project(page, home_url, session_name=None, working_dir=None):
    """Launch a session, setwd to `working_dir` (default RSTUDIO_WORKING_DIR)
    and create a project from that directory, checking the working
    directory before and after. Raises ValueError if no directory is set.

    Returns SessionAndProject(launch, project); project.elapsed_s is the
    project-creation time.
    """
    path = working_dir or WORKING_DIR
    if not path:
        raise ValueError(
            "launch_with_project() needs a working directory - pass working_dir= or set RSTUDIO_WORKING_DIR"
        )

    launch = launch_session(page, home_url, session_name=session_name)
    set_working_directory(page, path)
    verify_working_directory(page, path)
    project = create_project_from_existing_directory(page)
    verify_working_directory(page, path)
    return SessionAndProject(launch=launch, project=project)


def launch_with_project_and_text_file(
    page, home_url, session_name=None, working_dir=None, content=None, folder=None, file_name=None
):
    """launch_with_project(), then create one text file in the project
    (content/folder/file_name: see create_text_file()).

    Returns SessionProjectAndTextFile(launch, project, text_file);
    text_file.elapsed_s is the save time.
    """
    launch, project = launch_with_project(page, home_url, session_name=session_name, working_dir=working_dir)
    text_file = create_text_file(page, content=content, folder=folder, file_name=file_name)
    return SessionProjectAndTextFile(launch=launch, project=project, text_file=text_file)


def launch_with_project_and_text_files(
    page, home_url, session_name=None, working_dir=None, content=None, folder=None, file_count=1
):
    """launch_with_project(), then create `file_count` auto-named Text_<N>
    files one after another. Returns SessionProjectAndTextFiles(launch,
    project, text_files).
    """
    launch, project = launch_with_project(page, home_url, session_name=session_name, working_dir=working_dir)
    text_files = [create_text_file(page, content=content, folder=folder) for _ in range(file_count)]
    return SessionProjectAndTextFiles(launch=launch, project=project, text_files=text_files)


def launch_in_tabs_with_project_and_text_file(
    context, home_url, count=None, working_dir=None, content=None, folder=None, file_name=None
):
    """launch_with_project_and_text_file() `count` times, each in a new tab
    of `context` (tabs left open; a failed tab does not stop the rest).

    Names are reserved up front: once a session has a project its row shows
    the project name, so scanning per tab would reuse names.
    Returns (results, tabs, run_elapsed_s).
    """
    count = count or default_session_count()
    names_page = context.pages[0] if context.pages else context.new_page()
    session_names = next_auto_perf_session_names(names_page, count, home_url=home_url)

    results, tabs = [], []
    run_started = time.time()
    for i, session_name in enumerate(session_names):
        tab = context.new_page()
        tabs.append(tab)
        try:
            goto_session_list(tab, home_url)
            with allure_step("tab %d/%d: launch session + project + text file" % (i + 1, count)):
                outcome = launch_with_project_and_text_file(
                    tab, home_url, session_name=session_name,
                    working_dir=working_dir, content=content, folder=folder, file_name=file_name,
                )
            results.append({
                "index": i + 1,
                "ok": True,
                "session_name": session_name,
                "launch_elapsed_s": outcome.launch.elapsed_s,
                "project_elapsed_s": outcome.project.elapsed_s,
                "text_file_elapsed_s": outcome.text_file.elapsed_s,
                "bytes_received": outcome.launch.bytes_received,
            })
        except Exception as exc:
            print("[rstudio-local] tab %d/%d failed: %s" % (i + 1, count, exc))
            results.append({"index": i + 1, "ok": False, "session_name": session_name, "error": str(exc)})
    return results, tabs, time.time() - run_started


def open_sessions_in_tabs(context, home_url, session_names):
    """Reopen each existing session in its own new tab of `context`. A
    failed session is recorded and does not stop the rest.

    Returns (tabs, results): the tabs in order (caller closes them) and one
    {"index", "session_name", "ok"[, "error"]} dict per session.
    """
    tabs, results = [], []
    for i, session_name in enumerate(session_names):
        tab = context.new_page()
        tabs.append(tab)
        result = {"index": i + 1, "session_name": session_name, "ok": True}
        try:
            open_existing_session(tab, home_url, session_name)
        except Exception as exc:
            result.update(ok=False, error=str(exc))
        results.append(result)
    return tabs, results


def default_run_script_command():
    """R_SOURCE_COMMAND_NEW_SESSIONS from .env (no default)."""
    return env_path("R_SOURCE_COMMAND_NEW_SESSIONS", "RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND")


def default_run_script_timeout_ms():
    """RSTUDIO_RUN_SCRIPT_TIMEOUT_MS (default 300000)."""
    return int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))


def launch_and_run_script(
    page, home_url, source_command=None, session_name=None, working_dir=None,
    timeout_ms=None, run_count=1, think_time_s=0.0, script_runs=None,
):
    """Launch a new session (next Run_R_session_<N> by default), setwd to
    `working_dir` (default RSTUDIO_WORKING_DIR) and run source_command
    (default RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND) `run_count` times, pausing
    think_time_s between runs. Each run is recorded in `script_runs`.

    Returns {"session_name", "launch_elapsed_s", "bytes_received",
    "run_elapsed_s": [one per run]}.
    """
    source_command = source_command or default_run_script_command()
    timeout_ms = timeout_ms or default_run_script_timeout_ms()
    session_name = session_name or next_session_name(page, RUN_R_SESSION_NAME_PREFIX, home_url=home_url)

    launch = launch_session(page, home_url, session_name=session_name)
    set_session_working_directory(page, path=working_dir)
    run_elapsed_s = run_timed_console_command_repeatedly(
        page, launch.session_name, source_command, timeout_ms, run_count, think_time_s, script_runs
    )
    return {
        "session_name": launch.session_name,
        "launch_elapsed_s": launch.elapsed_s,
        "bytes_received": launch.bytes_received,
        "run_elapsed_s": run_elapsed_s,
    }
