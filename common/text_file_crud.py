"""A text file's whole life in an open RStudio session, as one loop:
create and save it -> open it -> edit and save it -> delete it, each step
timed and its result checked on the server. Used by
test_create_edit_open_delete_text_file (one session) and
test_concurrent_file_crud (many at once).

Every step gives a run record (common.script_timings) with "script" = the
step and "loop" = the loop number, so the session time report groups the
steps the way it groups scripts.
"""
import posixpath
import time

from common.rstudio_console_commands import r_string, read_text_file, run_console_command, wait_for_console_ready
from common.rstudio_files import close_all_editor_files
from common.rstudio_text_operations import (
    TEXT_FILE_SAVE_FOLDER,
    append_and_save_active_tab,
    create_text_file,
    delete_file,
    open_file_in_editor,
)
from common.session_actions import WORKING_DIR, set_session_working_directory
from config.config import env

CREATE_CONTENT = env("TEXT_FILE_CREATE_CONTENT", "Line 1: created by playwright")
EDIT_CONTENT = env("TEXT_FILE_EDIT_CONTENT", "Line 2: added by playwright")
STEP_TIMEOUT_MS = int(env("TEXT_FILE_STEP_TIMEOUT_MS", "30000"))
# How long to keep starting new loops, and the wait after each delete.
LOOP_DURATION_S = float(env("TEXT_FILE_LOOP_DURATION_S", "600"))
LOOP_WAIT_S = float(env("TEXT_FILE_LOOP_WAIT_S", "30"))
# A session stops looping after this many failed loops in a row.
MAX_FAILURES_IN_A_ROW = int(env("TEXT_FILE_LOOP_MAX_FAILURES_IN_A_ROW", "3"))
STEPS = ("create", "open", "edit", "delete")


def text_file_folder(working_dir=None):
    """<working dir>/auto_test, where the Save File dialog saves the files."""
    return posixpath.join(working_dir or WORKING_DIR, TEXT_FILE_SAVE_FOLDER)


def prepare_session(page, session_name, working_dir=None):
    """Get a freshly launched session ready for the loops: console ready, no
    files open, working directory and Files pane at `working_dir` (default
    RSTUDIO_WORKING_DIR) and its auto_test folder there - the Save File
    dialog opens in the Files pane's folder and double-clicks auto_test."""
    working_dir = working_dir or WORKING_DIR
    wait_for_console_ready(page)
    close_all_editor_files(page, session_name)
    set_session_working_directory(page, working_dir)
    run_console_command(page, "dir.create(%s, showWarnings = FALSE)" % r_string(text_file_folder(working_dir)),
                        timeout_ms=STEP_TIMEOUT_MS)
    run_console_command(page, "if (exists('.rs.api.filesPaneNavigate')) .rs.api.filesPaneNavigate(%s)"
                        % r_string(working_dir), timeout_ms=STEP_TIMEOUT_MS)


def _steps(page, session_name, path):
    """(step, fn) for one loop; each fn checks its result and returns the
    step's seconds."""
    def create():
        created = create_text_file(page, content=CREATE_CONTENT, file_name=posixpath.basename(path),
                                   timeout_ms=STEP_TIMEOUT_MS)
        on_disk = read_text_file(page, path)
        assert on_disk is not None, "%s was not created (saved in %s?)" % (path, created.folder)
        assert on_disk.strip() == CREATE_CONTENT, "%s has %r, expected %r" % (path, on_disk, CREATE_CONTENT)
        close_all_editor_files(page, session_name)
        return created.elapsed_s

    def open_():
        open_s, shown = open_file_in_editor(page, path, timeout_ms=STEP_TIMEOUT_MS)
        shown = [line for line in shown if line.strip()]
        assert shown == [CREATE_CONTENT], "the editor shows %r for %s" % (shown, path)
        return open_s

    def edit():  # in the tab open_() left active
        edit_s = append_and_save_active_tab(page, EDIT_CONTENT, path, timeout_ms=STEP_TIMEOUT_MS)
        lines = (read_text_file(page, path) or "").strip().splitlines()
        assert lines == [CREATE_CONTENT, EDIT_CONTENT], "%s has %r after the edit" % (path, lines)
        close_all_editor_files(page, session_name)
        return edit_s

    def delete():
        return delete_file(page, path, timeout_ms=STEP_TIMEOUT_MS)

    return zip(STEPS, (create, open_, edit, delete))


def run_file_loop(page, session_name, path, loop):
    """One loop on the file `path`. Returns (records, error): a run record
    per step done (the failed one with its status, ended None) and the
    "<step> failed: <reason>" that stopped the loop, else None. After a
    failure the tab is closed and the file deleted, so the next loop starts
    clean."""
    records = []
    for step, fn in _steps(page, session_name, path):
        record = {"session_name": session_name, "script": step, "loop": loop, "started": time.time(),
                  "ended": None, "status": "not run"}
        records.append(record)
        try:
            seconds = fn()
        except Exception as exc:
            reason = " ".join(str(exc).split())[:300]
            record["status"] = "failed: %s" % reason
            close_all_editor_files(page, session_name)
            try:
                run_console_command(page, "unlink(%s)" % r_string(path), timeout_ms=STEP_TIMEOUT_MS)
            except Exception as cleanup_exc:
                print("\n[rstudio-local] could not delete %s: %s" % (path, cleanup_exc))
            return records, "%s failed: %s" % (step, reason)
        # the step's own time (e.g. Save click to dialog closed), not its checks
        record["ended"], record["status"] = record["started"] + seconds, "ok"
    return records, None


def describe_loop(records):
    """'create 0.10s, open 3.24s, ...' for a loop's records."""
    return ", ".join("%s %.2fs" % (r["script"], r["ended"] - r["started"]) if r["status"] == "ok"
                     else "%s FAILED" % r["script"] for r in records)


def step_summary(records):
    """'create min 0.10 / avg 0.14 / max 0.17 s; ...' over the ok records."""
    parts = []
    for step in STEPS:
        times = [r["ended"] - r["started"] for r in records if r.get("script") == step and r["status"] == "ok"]
        if times:
            parts.append("%s min %.2f / avg %.2f / max %.2f s"
                         % (step, min(times), sum(times) / len(times), max(times)))
    return "; ".join(parts)
