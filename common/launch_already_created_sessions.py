"""Open sessions that already exist on Posit Workbench (e.g. ones created
through the API by common.api_helper) in the browser, one tab per session,
and run an R script in all of them at once.

The tabs stay open so callers can keep working in each session's IDE; close
them with close_launched_sessions() when done. The sessions keep running.
"""
import os
import posixpath
from collections import namedtuple

from common.config import EVIDENCE_DIR, env
from common.rstudio_console_commands import (
    get_console_output,
    r_source_command,
    read_text_file,
    wait_for_console_ready,
)
from common.rstudio_session_helper import is_session_alive, open_existing_session
from common.rstudio_workbenchjob import check_output_file
from common.script_timings import new_run_record, submit_in_each, wait_for_console_runs
from common.session_actions import close_all_editor_files, recreate_session_folder, set_session_working_directory

API_SESSION_NAME_PREFIX = "AUTO_API_SESSION_"
API_SESSION_START = int(env("API_SESSION_START", "105"))
API_SESSION_END = int(env("API_SESSION_END", "109"))

# A suspended session is resumed when opened, which takes longer than
# joining an Active one.
OPEN_SESSION_TIMEOUT_MS = int(env("OPEN_SESSION_TIMEOUT_MS", "120000"))
# How long to wait, after the IDE shows, for the console to answer commands
# (a resumed session may still be restoring its workspace).
CONSOLE_READY_TIMEOUT_MS = int(env("CONSOLE_READY_TIMEOUT_MS", "120000"))

# page is the tab showing the session's IDE (None if it failed to open);
# launch is the SessionLaunch from open_existing_session(); error is None
# on success, else why the session could not be opened.
LaunchedSession = namedtuple("LaunchedSession", ["session_name", "page", "launch", "error"])


def api_session_names(start=API_SESSION_START, end=API_SESSION_END, prefix=API_SESSION_NAME_PREFIX):
    """[<prefix><start>, ..., <prefix><end>], end inclusive. Defaults to
    AUTO_API_SESSION_105 .. AUTO_API_SESSION_109."""
    return ["%s%d" % (prefix, n) for n in range(start, end + 1)]


def launch_already_created_session(context, home_url, session_name, timeout_ms=OPEN_SESSION_TIMEOUT_MS):
    """Open the existing session `session_name` in a new tab of `context`
    and wait for its IDE and for the console to answer commands. Returns a
    LaunchedSession; the tab stays open on success and is closed on failure.
    """
    page = context.new_page()
    try:
        launch = open_existing_session(page, home_url, session_name, timeout_ms=timeout_ms)
        if not is_session_alive(page):
            raise RuntimeError("session %s did not show its IDE after opening" % session_name)
        ready_s = wait_for_console_ready(page, timeout_ms=CONSOLE_READY_TIMEOUT_MS)
        close_all_editor_files(page, session_name)
    except Exception as exc:
        page.close()
        print("\n[rstudio-local] session %s could not be opened: %s" % (session_name, exc))
        return LaunchedSession(session_name=session_name, page=None, launch=None, error=str(exc))

    print(
        "\n[rstudio-local] session %s opened in %.2fs, console ready %.2fs later"
        % (session_name, launch.elapsed_s, ready_s)
    )
    return LaunchedSession(session_name=session_name, page=page, launch=launch, error=None)


def launch_already_created_sessions(context, home_url, session_names=None, timeout_ms=OPEN_SESSION_TIMEOUT_MS):
    """launch_already_created_session() for each name in `session_names`
    (default api_session_names()), each in its own tab. One session failing
    to open does not stop the rest.

    Returns one LaunchedSession per name, in order. Check `.error` for
    failures and pass the result to close_launched_sessions() when done.
    """
    return [
        launch_already_created_session(context, home_url, name, timeout_ms=timeout_ms)
        for name in (session_names if session_names is not None else api_session_names())
    ]


def run_script_in_launched_sessions(launched, script_path, working_dir=None, timeout_ms=1800000,
                                    poll_interval_ms=500, output_path=None, per_session_dir=False,
                                    delete_output=False, on_poll=None):
    """In each session from launch_already_created_sessions(), setwd to
    `working_dir` (default RSTUDIO_WORKING_DIR; skipped if neither is set),
    or with per_session_dir to its own `working_dir`/<session name> folder
    (emptied first: data left there by an earlier run is deleted, see
    recreate_session_folder()). Then source `script_path` in every console and
    wait for all runs together, so the scripts run concurrently.

    With `output_path` (relative paths resolve against the working
    directory), a run only counts as "ok" if that file exists once the
    script has finished; with delete_output it is then deleted (and the
    per-session folder too, if left empty). on_poll, if given, is called
    once per polling round so callers can drive other sessions meanwhile;
    keep it quick. Tabs are left open.

    Returns one run record per session (see common.script_timings); sessions
    that failed to open are recorded as such.
    """
    if per_session_dir and not working_dir:
        raise ValueError("per_session_dir needs a working_dir")
    source_command = r_source_command(script_path)
    runs, ready = [], []

    for session in launched:
        record = new_run_record(session.session_name)
        runs.append(record)
        if session.error:
            record["status"] = "open failed: %s" % session.error
            continue
        session_dir = posixpath.join(working_dir, session.session_name) if per_session_dir else None
        try:
            if session_dir:
                recreate_session_folder(session.page, working_dir, session.session_name)
            set_session_working_directory(session.page, path=session_dir or working_dir)
            ready.append((session.page, record, session_dir))
        except Exception as exc:
            record["status"] = "setwd failed: %s" % exc

    def _on_done(page, record, session_dir):
        print(
            "\n[rstudio-local] session %s: %s took %.2fs"
            % (record["session_name"], source_command, record["ended"] - record["started"])
        )
        save_screenshot(page, "console_output_%s.png" % record["session_name"])
        if output_path:
            save_output_file(page, record["session_name"], output_path)
            check_output_file(page, record, output_path, delete=delete_output, remove_dir=session_dir)

    pending = submit_in_each(ready, source_command)
    for page, _, record, _ in pending:
        save_screenshot(page, "console_command_%s.png" % record["session_name"])
    wait_for_console_runs(
        pending, timeout_ms, poll_interval_ms, on_done=_on_done,
        on_timeout=lambda page, record, _: report_console_timeout(page, record["session_name"]), on_poll=on_poll,
    )
    return runs


def save_screenshot(page, file_name):
    """Save a screenshot of `page` to evidence/`file_name`. Errors are
    printed, not raised."""
    path = os.path.join(EVIDENCE_DIR, file_name)
    try:
        page.screenshot(path=path)
        print("\n[rstudio-local] screenshot saved to %s" % path)
    except Exception as exc:
        print("\n[rstudio-local] could not save screenshot %s: %s" % (path, exc))


def save_output_file(page, session_name, path):
    """Copy the text file `path` (e.g. a script's CSV output) from the
    session to evidence/output_<session name>_<file name>. Errors are
    printed, not raised."""
    local_path = os.path.join(EVIDENCE_DIR, "output_%s_%s" % (session_name, posixpath.basename(path)))
    try:
        content = read_text_file(page, path)
        if content is None:
            print("\n[rstudio-local] session %s: %s not found, nothing to save" % (session_name, path))
            return
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(local_path, "w", encoding="utf-8", newline="") as f:
            f.write(content.replace("\r\n", "\n").rstrip("\n") + "\n")
        print("\n[rstudio-local] session %s: output saved to %s" % (session_name, local_path))
    except Exception as exc:
        print("\n[rstudio-local] session %s: could not save %s: %s" % (session_name, path, exc))


def report_console_timeout(page, session_name, tail_lines=15):
    """Print the end of a timed-out session's console and save a screenshot
    of its tab to evidence/console_timeout_<session name>.png, to show
    whether the command was never submitted, errored or is still running.
    Errors are printed, not raised.
    """
    try:
        tail = get_console_output(page).strip().splitlines()[-tail_lines:]
        print("\n[rstudio-local] session %s timed out; console ends with:\n%s" % (session_name, "\n".join(tail)))
        path = os.path.join(EVIDENCE_DIR, "console_timeout_%s.png" % session_name)
        page.screenshot(path=path)
        print("[rstudio-local] screenshot saved to %s" % path)
    except Exception as exc:
        print("\n[rstudio-local] session %s timed out; could not capture its console: %s" % (session_name, exc))


def close_launched_sessions(launched):
    """Close the tabs opened by launch_already_created_sessions(). The
    sessions themselves keep running on the server."""
    for session in launched:
        if session.page is not None:
            try:
                session.page.close()
            except Exception:
                pass  # already closed (e.g. the browser went away)
