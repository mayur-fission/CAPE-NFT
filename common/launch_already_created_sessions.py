"""Open sessions that already exist on Posit Workbench (e.g. ones created
through the API by common.api_helper) in the browser, one tab per session.

The tabs are left open so callers can carry on in each session's IDE -
run an R script, setwd, check files - and close them with
close_launched_sessions() when done.
"""
import posixpath
import time
from collections import namedtuple

from common.config import env
from common.rstudio_console_commands import is_console_command_done, r_string, submit_console_command
from common.rstudio_session_helper import is_session_alive, open_existing_session
from common.rstudio_workbenchjob import _check_output_file
from common.session_actions import create_folder, set_session_working_directory

API_SESSION_NAME_PREFIX = "AUTO_API_SESSION_"
API_SESSION_START = int(env("API_SESSION_START", "105"))
API_SESSION_END = int(env("API_SESSION_END", "109"))

# A suspended session is resumed when opened, which takes longer than
# joining an Active one.
OPEN_SESSION_TIMEOUT_MS = int(env("OPEN_SESSION_TIMEOUT_MS", "120000"))

# page is the tab showing the session's IDE (None if it failed to open);
# launch is the SessionLaunch from open_existing_session(); error is None
# on success, else why the session could not be opened.
LaunchedSession = namedtuple("LaunchedSession", ["session_name", "page", "launch", "error"])


def api_session_names(start=API_SESSION_START, end=API_SESSION_END, prefix=API_SESSION_NAME_PREFIX):
    """[<prefix><start>, ..., <prefix><end>], end inclusive. Defaults to
    AUTO_API_SESSION_105 .. AUTO_API_SESSION_109.
    """
    return ["%s%d" % (prefix, n) for n in range(start, end + 1)]


def launch_already_created_session(context, home_url, session_name, timeout_ms=OPEN_SESSION_TIMEOUT_MS):
    """Open the existing session `session_name` in a new tab of `context`
    and wait for its IDE. Returns a LaunchedSession; the tab stays open on
    success and is closed on failure.
    """
    page = context.new_page()
    try:
        launch = open_existing_session(page, home_url, session_name, timeout_ms=timeout_ms)
        if not is_session_alive(page):
            raise RuntimeError("session %s did not show its IDE after opening" % session_name)
    except Exception as exc:
        page.close()
        print("\n[rstudio-local] session %s could not be opened: %s" % (session_name, exc))
        return LaunchedSession(session_name=session_name, page=None, launch=None, error=str(exc))

    print("\n[rstudio-local] session %s opened in %.2fs" % (session_name, launch.elapsed_s))
    return LaunchedSession(session_name=session_name, page=page, launch=launch, error=None)


def launch_already_created_sessions(context, home_url, session_names=None, timeout_ms=OPEN_SESSION_TIMEOUT_MS):
    """launch_already_created_session() for each name in `session_names`
    (default api_session_names()), each in its own tab. One session failing
    to open does not stop the rest.

    Returns one LaunchedSession per name, in order. Check `.error` for
    failures and pass the result to close_launched_sessions() when done.
    """
    if session_names is None:
        session_names = api_session_names()
    return [
        launch_already_created_session(context, home_url, name, timeout_ms=timeout_ms)
        for name in session_names
    ]


def run_script_in_launched_sessions(launched, script_path, working_dir=None, timeout_ms=1800000,
                                    poll_interval_ms=500, output_path=None, per_session_dir=False,
                                    delete_output=False):
    """In each session opened by launch_already_created_sessions(), setwd to
    `working_dir` (default RSTUDIO_WORKING_DIR; skipped if neither is set),
    or with per_session_dir to its own `working_dir`/<session name> folder
    (created if missing). Then source `script_path` in every console and
    poll the tabs in turn until every run has finished or timed out, so the
    scripts run concurrently. With `output_path` (relative paths resolve
    against the working directory), a run only counts as "ok" if that file
    exists once the script has finished; with delete_output it is then
    deleted (and the per-session folder too, if left empty). Tabs are left
    open.

    Returns one record per session in the common.script_timings format
    ("session_name", "started", "ended", "status"), status "ok", "timed out"
    or the error. Sessions that failed to open are recorded as such.
    """
    if per_session_dir and not working_dir:
        raise ValueError("per_session_dir needs a working_dir")
    source_command = "source(%s)" % r_string(script_path)
    runs, ready, pending = [], [], []

    for session in launched:
        record = {"session_name": session.session_name, "started": None, "ended": None, "status": "not run"}
        runs.append(record)
        if session.error:
            record["status"] = "open failed: %s" % session.error
            continue
        session_dir = posixpath.join(working_dir, session.session_name) if per_session_dir else None
        try:
            if session_dir:
                create_folder(session.page, session_dir)
            set_session_working_directory(session.page, path=session_dir or working_dir)
            ready.append((session.page, record, session_dir))
        except Exception as exc:
            record["status"] = "setwd failed: %s" % exc

    for page, record, session_dir in ready:
        try:
            marker, record["started"] = submit_console_command(page, source_command)
            pending.append((page, marker, record, session_dir))
        except Exception as exc:
            record["status"] = "submit failed: %s" % exc

    while pending:
        for item in list(pending):
            page, marker, record, session_dir = item
            try:
                done = is_console_command_done(page, marker)
            except Exception as exc:
                record["status"] = str(exc)
                pending.remove(item)
                continue
            if done:
                record["ended"], record["status"] = time.time(), "ok"
                pending.remove(item)
                print(
                    "\n[rstudio-local] session %s: %s took %.2fs"
                    % (record["session_name"], source_command, record["ended"] - record["started"])
                )
                if output_path:
                    _check_output_file(page, record, output_path, delete=delete_output, remove_dir=session_dir)
            elif time.time() - record["started"] >= timeout_ms / 1000.0:
                record["status"] = "timed out"
                pending.remove(item)
        if pending:
            pending[0][0].wait_for_timeout(poll_interval_ms)
    return runs


def close_launched_sessions(launched):
    """Close the tabs opened by launch_already_created_sessions(). The
    sessions themselves keep running on the server.
    """
    for session in launched:
        if session.page is not None:
            try:
                session.page.close()
            except Exception:
                pass  # already closed (e.g. the browser went away)
