"""Multi-step scenarios that run on one session at a time, built from the
one-shot actions in common/session_actions.py.

For concurrent runs see common/session_concurrent.py; for sequential runs
on one page see common/session_batch.py.
"""
import time
from collections import namedtuple

from common.config import env
from common.rstudio_workbench import create_project_from_existing_directory as _create_project_from_existing_directory
from common.rstudio_workbench import set_working_directory as _set_working_directory
from common.rstudio_workbench import verify_working_directory as _verify_working_directory
from common.rstudio_text_operations import create_text_file as _create_text_file
from common.rstudio_session_helper import next_auto_perf_session_names as _next_auto_perf_session_names
from common.rstudio_session_helper import next_session_name as _next_session_name
from common.rstudio_session_helper import next_session_names as _next_session_names
from common.rstudio_session_helper import open_existing_session as _open_existing_session
from common.rstudio_session_helper import RUN_R_SESSION_NAME_PREFIX as _RUN_R_SESSION_NAME_PREFIX

from config.perf_report import allure_step

from common.script_timings import run_timed_console_command

from common.session_actions import WORKING_DIR
from common.session_actions import launch_session
from common.session_actions import set_session_working_directory

SessionAndProject = namedtuple("SessionAndProject", ["launch", "project"])


def launch_with_project(page, home_url, session_name=None, working_dir=None):
    """Launch a session, set its working directory, and create a project
    from that directory, verifying the working directory before and after.

    working_dir defaults to RSTUDIO_WORKING_DIR; raises ValueError if
    neither is set.

    Returns SessionAndProject(launch, project), where project.elapsed_s is
    the project-creation time.
    """
    path = working_dir or WORKING_DIR
    if not path:
        raise ValueError(
            "launch_with_project() needs a working directory to "
            "create the project in and verify against - pass working_dir= or "
            "set RSTUDIO_WORKING_DIR"
        )

    launch = launch_session(page, home_url, session_name=session_name)

    _set_working_directory(page, path)
    _verify_working_directory(page, path)

    project = _create_project_from_existing_directory(page)

    _verify_working_directory(page, path)

    return SessionAndProject(launch=launch, project=project)


SessionProjectAndTextFile = namedtuple("SessionProjectAndTextFile", ["launch", "project", "text_file"])


def launch_with_project_and_text_file(
    page, home_url, session_name=None, working_dir=None, content=None, folder=None, file_name=None
):
    """launch_with_project(), then create one text file in the project.

    content/folder/file_name pass through to create_text_file() in
    common/rstudio_text_operations.py.

    Returns SessionProjectAndTextFile(launch, project, text_file), where
    text_file.elapsed_s is the save time.
    """
    launch, project = launch_with_project(
        page, home_url, session_name=session_name, working_dir=working_dir
    )

    kwargs = {}
    if content is not None:
        kwargs["content"] = content
    if folder is not None:
        kwargs["folder"] = folder
    if file_name is not None:
        kwargs["file_name"] = file_name
    text_file = _create_text_file(page, **kwargs)

    return SessionProjectAndTextFile(launch=launch, project=project, text_file=text_file)


SessionProjectAndTextFiles = namedtuple("SessionProjectAndTextFiles", ["launch", "project", "text_files"])


def launch_with_project_and_text_files(
    page, home_url, session_name=None, working_dir=None, content=None, folder=None, file_count=1
):
    """launch_with_project(), then create `file_count` text files in the
    project one after another. Each file gets an auto-generated Text_<N>
    name.

    Returns SessionProjectAndTextFiles(launch, project, text_files).
    """
    launch, project = launch_with_project(
        page, home_url, session_name=session_name, working_dir=working_dir
    )

    kwargs = {}
    if content is not None:
        kwargs["content"] = content
    if folder is not None:
        kwargs["folder"] = folder

    text_files = [_create_text_file(page, **kwargs) for _ in range(file_count)]

    return SessionProjectAndTextFiles(launch=launch, project=project, text_files=text_files)


def launch_in_tabs_with_project_and_text_file(
    context, home_url, count=None, working_dir=None, content=None, folder=None, file_name=None
):
    """Run launch_with_project_and_text_file() `count` times, each in its own
    new tab of `context` (already signed in via shared cookies).

    All AUTO_PERF_SESSION_<N> names are reserved up front: once a session
    has a project its row shows the project name instead, so scanning per
    tab would reuse names.

    A failed tab is recorded and does not stop the rest. Tabs are left open;
    closing them and cleaning up sessions is the caller's job. count
    defaults to RSTUDIO_SESSION_COUNT.

    Returns (results, tabs, run_elapsed_s): one result dict per session
    ({"index", "ok", "session_name", "launch_elapsed_s", "project_elapsed_s",
    "text_file_elapsed_s", "bytes_received"} or {"index", "ok": False,
    "error"}), the tabs in the same order, and total wall-clock seconds.
    """
    if count is None:
        count = int(env("RSTUDIO_SESSION_COUNT", "10"))

    names_page = context.pages[0] if context.pages else context.new_page()
    session_names = _next_auto_perf_session_names(names_page, count, home_url=home_url)

    results = []
    tabs = []

    run_started = time.time()
    for i in range(count):
        tab = context.new_page()
        tabs.append(tab)
        try:
            tab.goto(home_url)
            tab.get_by_text("New Session", exact=True).first.wait_for(state="visible", timeout=30000)
            tab.wait_for_timeout(1500)  # the session table re-renders shortly after

            with allure_step("tab %d/%d: launch session + project + text file" % (i + 1, count)):
                outcome = launch_with_project_and_text_file(
                    tab, home_url, session_name=session_names[i],
                    working_dir=working_dir, content=content, folder=folder, file_name=file_name
                )

            results.append({
                "index": i + 1,
                "ok": True,
                "session_name": outcome.launch.session_name,
                "launch_elapsed_s": outcome.launch.elapsed_s,
                "project_elapsed_s": outcome.project.elapsed_s,
                "text_file_elapsed_s": outcome.text_file.elapsed_s,
                "bytes_received": outcome.launch.bytes_received,
            })
        except Exception as exc:
            print("[rstudio-local] tab %d/%d failed: %s" % (i + 1, count, exc))
            results.append({"index": i + 1, "ok": False, "error": str(exc)})
    run_elapsed = time.time() - run_started

    return results, tabs, run_elapsed


def open_sessions_in_tabs(context, home_url, session_names):
    """Reopen each existing session in `session_names` in its own new tab
    of `context`. A failed session is recorded and does not stop the rest.

    Returns (tabs, results): the tabs in order (caller closes them) and one
    {"index", "session_name", "ok"[, "error"]} dict per session.
    """
    tabs = []
    results = []
    for i, session_name in enumerate(session_names):
        tab = context.new_page()
        tabs.append(tab)
        try:
            _open_existing_session(tab, home_url, session_name)
            results.append({"index": i + 1, "session_name": session_name, "ok": True})
        except Exception as exc:
            results.append({"index": i + 1, "session_name": session_name, "ok": False, "error": str(exc)})
    return tabs, results


def next_run_r_session_name(page, home_url=None):
    """Return the next Run_R_session_<N> name."""
    return _next_session_name(page, _RUN_R_SESSION_NAME_PREFIX, home_url=home_url)


def next_run_r_session_names(page, count, home_url=None):
    """Reserve `count` consecutive Run_R_session_<N> names at once, so
    concurrent browsers don't race for the same name.
    """
    return _next_session_names(page, _RUN_R_SESSION_NAME_PREFIX, count, home_url=home_url)


def launch_and_run_script(
    page, home_url, source_command=None, session_name=None, working_dir=None,
    timeout_ms=None, run_count=1, think_time_s=0.0, script_runs=None,
):
    """Launch a new session (Run_R_session_<N> by default), set its working
    directory, and run source_command in the console `run_count` times,
    sleeping think_time_s between runs. Each run is recorded in
    `script_runs` (see common/script_timings.py).

    source_command/timeout_ms default to RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND/
    RSTUDIO_RUN_SCRIPT_TIMEOUT_MS; working_dir to RSTUDIO_WORKING_DIR.

    Returns {"session_name", "launch_elapsed_s", "bytes_received",
    "run_elapsed_s": [one per run]}.
    """
    source_command = source_command or env("RSTUDIO_RUN_SCRIPT_SOURCE_COMMAND", "source('sample_10mb.R')")
    timeout_ms = timeout_ms or int(env("RSTUDIO_RUN_SCRIPT_TIMEOUT_MS", "300000"))
    session_name = session_name or next_run_r_session_name(page, home_url=home_url)

    launch = launch_session(page, home_url, session_name=session_name)
    set_session_working_directory(page, path=working_dir)

    run_elapsed_s = []
    for i in range(run_count):
        run_elapsed_s.append(run_timed_console_command(
            page, launch.session_name, source_command, timeout_ms, script_runs
        ))
        if think_time_s and i + 1 < run_count:
            time.sleep(think_time_s)

    return {
        "session_name": launch.session_name,
        "launch_elapsed_s": launch.elapsed_s,
        "bytes_received": launch.bytes_received,
        "run_elapsed_s": run_elapsed_s,
    }
