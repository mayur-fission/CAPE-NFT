"""A text file's whole life in an RStudio Pro session, over and over for
TEXT_FILE_LOOP_DURATION_S: create and save it, open it, edit and save it,
delete it, then wait TEXT_FILE_LOOP_WAIT_S before the next file. Every step
is timed and its result checked on the server.

Each loop's file is saved in RSTUDIO_WORKING_DIR/auto_test (created if
missing) as text_file_<run start>_<loop>.txt, so files never collide. A
failed loop is recorded, its file deleted, and the next loop starts after
the wait; TEXT_FILE_LOOP_MAX_FAILURES_IN_A_ROW failed loops in a row end the
run early. The session is force-quit at the end.

    pytest tests/test_create_edit_open_delete_text_file.py -v -m rstudio_local
"""
import os
import posixpath
import statistics as st
import time
from datetime import datetime

import pytest

from common.perf_report import allure_step, write_csv_report
from common.rstudio_console_commands import r_string, read_text_file, run_console_command, wait_for_console_ready
from common.rstudio_files import close_all_editor_files
from common.rstudio_text_operations import (
    TEXT_FILE_SAVE_FOLDER,
    append_and_save_active_tab,
    create_text_file,
    delete_file,
    open_file_in_editor,
)
from common.session_actions import WORKING_DIR, launch_session, login_and_snapshot, set_session_working_directory
from common.session_cleanup import cleanup_and_verify_sessions
from config.config import env, evidence_dir

pytestmark = pytest.mark.rstudio_local

CREATE_CONTENT = env("TEXT_FILE_CREATE_CONTENT", "Line 1: created by playwright")
EDIT_CONTENT = env("TEXT_FILE_EDIT_CONTENT", "Line 2: added by playwright")
STEP_TIMEOUT_MS = int(env("TEXT_FILE_STEP_TIMEOUT_MS", "30000"))
# How long to keep starting new loops, and the wait after each delete.
LOOP_DURATION_S = float(env("TEXT_FILE_LOOP_DURATION_S", "600"))
LOOP_WAIT_S = float(env("TEXT_FILE_LOOP_WAIT_S", "30"))
MAX_FAILURES_IN_A_ROW = int(env("TEXT_FILE_LOOP_MAX_FAILURES_IN_A_ROW", "3"))
RUN_STAMP = time.strftime("%Y%m%d-%H%M%S")
STEPS = ("create", "open", "edit", "delete")


def _run_loop(page, session_name, path, record, state):
    """One file's life: create and save -> open -> edit and save -> delete.
    record(step, started, seconds) is called after each step that worked;
    raises on the first that does not, with state["step"] naming it."""
    def timed(step, fn):
        started = time.time()
        seconds = fn()
        record(step, started, seconds)

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

    def edit():  # in the tab open() left active
        edit_s = append_and_save_active_tab(page, EDIT_CONTENT, path, timeout_ms=STEP_TIMEOUT_MS)
        lines = (read_text_file(page, path) or "").strip().splitlines()
        assert lines == [CREATE_CONTENT, EDIT_CONTENT], "%s has %r after the edit" % (path, lines)
        close_all_editor_files(page, session_name)
        return edit_s

    def delete():
        return delete_file(page, path, timeout_ms=STEP_TIMEOUT_MS)

    for step, fn in zip(STEPS, (create, open_, edit, delete)):
        state["step"] = step
        timed(step, fn)



def test_create_edit_open_delete_text_file(page):
    """Every loop's create, open, edit and delete works, for
    TEXT_FILE_LOOP_DURATION_S. Step times go to
    evidence/.../rstudio_text_file_steps.csv (attached to Allure), rewritten
    after every loop so a stopped run keeps its results.

    Flow: login -> launch new session -> setwd, Files pane to it -> loop
    until the duration is up: [create text file (File > New File > Text
    File, type, save) -> close it -> open it (file.edit) -> edit it (type,
    Ctrl+S) -> close it -> delete it -> wait] -> quit session
    """
    if not WORKING_DIR:
        pytest.skip("RSTUDIO_WORKING_DIR not set - the text files are saved under it")
    folder_path = posixpath.join(WORKING_DIR, TEXT_FILE_SAVE_FOLDER)
    csv_path = os.path.join(evidence_dir(), "rstudio_text_file_steps.csv")
    home_url, before_ids = login_and_snapshot(page)
    rows = []  # [loop, step, start time, seconds, status]
    failures = []  # "loop <n>: <step> failed: <error>"
    session_name = None

    def write_rows():
        write_csv_report(csv_path, [["Loop", "Step", "Start Time", "Time Taken (s)", "Status"]] + rows)

    try:
        with allure_step("Launch a session"):
            launch = launch_session(page, home_url)
            session_name = launch.session_name
            wait_for_console_ready(page)
            close_all_editor_files(page, session_name)
            set_session_working_directory(page, WORKING_DIR)
            # The Save File dialog opens in the Files pane's folder and
            # double-clicks TEXT_FILE_SAVE_FOLDER there, so it must exist.
            run_console_command(page, "dir.create(%s, showWarnings = FALSE)" % r_string(folder_path),
                                timeout_ms=STEP_TIMEOUT_MS)
            run_console_command(page, "if (exists('.rs.api.filesPaneNavigate')) .rs.api.filesPaneNavigate(%s)"
                                % r_string(WORKING_DIR), timeout_ms=STEP_TIMEOUT_MS)
        print("\n[rstudio-local] session %s launched in %.2fs; looping for %.0fs, %.0fs wait after each delete"
              % (session_name, launch.elapsed_s, LOOP_DURATION_S, LOOP_WAIT_S))

        deadline = time.time() + LOOP_DURATION_S
        loop = in_a_row = 0
        while time.time() < deadline:
            loop += 1
            path = posixpath.join(folder_path, "text_file_%s_%d.txt" % (RUN_STAMP, loop))

            def record(step, started, seconds, status="ok"):
                rows.append([loop, step, datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
                             "%.2f" % seconds if seconds is not None else "", status])

            loop_started, state = time.time(), {"step": None}
            with allure_step("Loop %d: %s" % (loop, posixpath.basename(path))):
                try:
                    _run_loop(page, session_name, path, record, state)
                    in_a_row = 0
                    print("\n[rstudio-local] loop %d ok: %s" % (loop, ", ".join(
                        "%s %ss" % (r[1], r[3]) for r in rows if r[0] == loop)))
                except Exception as exc:
                    in_a_row += 1
                    reason = " ".join(str(exc).split())[:300]
                    record(state["step"], loop_started, None, "failed: %s" % reason)
                    failures.append("loop %d: %s failed: %s" % (loop, state["step"], reason))
                    print("\n[rstudio-local] loop %d: %s failed: %s" % (loop, state["step"], reason))
                    # start the next loop clean: no open tab, no file left behind
                    close_all_editor_files(page, session_name)
                    try:
                        run_console_command(page, "unlink(%s)" % r_string(path), timeout_ms=STEP_TIMEOUT_MS)
                    except Exception as cleanup_exc:
                        print("\n[rstudio-local] could not delete %s: %s" % (path, cleanup_exc))
            write_rows()
            if in_a_row >= MAX_FAILURES_IN_A_ROW:
                print("\n[rstudio-local] %d loops failed in a row - stopping" % in_a_row)
                break
            if time.time() + LOOP_WAIT_S >= deadline:
                break  # the next loop would start after the duration is up
            page.wait_for_timeout(LOOP_WAIT_S * 1000)

        summary = []
        for step in STEPS:
            times = [float(r[3]) for r in rows if r[1] == step and r[4] == "ok"]
            if times:
                summary.append("%s min %.2f / avg %.2f / max %.2f s" % (step, min(times), st.mean(times), max(times)))
        print("\n[rstudio-local] %d loop(s), %d failed; %s" % (loop, len(failures), "; ".join(summary)))
        assert not failures, "loops that failed:\n%s" % "\n".join(failures)

    finally:
        if rows:
            write_rows()
        cleanup_and_verify_sessions(page, home_url, before_ids)
