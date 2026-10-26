"""A text file's whole life in an RStudio Pro session, over and over for
TEXT_FILE_LOOP_DURATION_S: create and save it, open it, edit and save it,
delete it, then wait TEXT_FILE_LOOP_WAIT_S before the next file. Every step
is timed and its result checked on the server (common/text_file_crud.py).

Each loop's file is saved in RSTUDIO_WORKING_DIR/auto_test (created if
missing) as text_file_<run start>_<loop>.txt, so files never collide. A
failed loop is recorded, its file deleted, and the next loop starts after
the wait; TEXT_FILE_LOOP_MAX_FAILURES_IN_A_ROW failed loops in a row end the
run early. The session is force-quit at the end. test_concurrent_file_crud
runs the same loop in many sessions at once.

    pytest tests/test_create_edit_open_delete_text_file.py -v -m rstudio_local
"""
import os
import posixpath
import time
from datetime import datetime

import pytest

from common.perf_report import allure_step, write_csv_report
from common.session_actions import WORKING_DIR, launch_session, login_and_snapshot
from common.session_cleanup import cleanup_and_verify_sessions
from common.text_file_crud import (
    LOOP_DURATION_S,
    LOOP_WAIT_S,
    MAX_FAILURES_IN_A_ROW,
    describe_loop,
    prepare_session,
    run_file_loop,
    step_summary,
    text_file_folder,
)
from config.config import evidence_dir

pytestmark = pytest.mark.rstudio_local

RUN_STAMP = time.strftime("%Y%m%d-%H%M%S")


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
    csv_path = os.path.join(evidence_dir(), "rstudio_text_file_steps.csv")
    home_url, before_ids = login_and_snapshot(page)
    records, failures = [], []

    def write_rows():
        write_csv_report(csv_path, [["Loop", "Step", "Start Time", "Time Taken (s)", "Status"]] + [
            [r["loop"], r["script"], datetime.fromtimestamp(r["started"]).strftime("%Y-%m-%d %H:%M:%S"),
             "%.2f" % (r["ended"] - r["started"]) if r["ended"] is not None else "", r["status"]]
            for r in records])

    try:
        with allure_step("Launch a session"):
            launch = launch_session(page, home_url)
            session_name = launch.session_name
            prepare_session(page, session_name)
        print("\n[rstudio-local] session %s launched in %.2fs; looping for %.0fs, %.0fs wait after each delete"
              % (session_name, launch.elapsed_s, LOOP_DURATION_S, LOOP_WAIT_S))

        deadline = time.time() + LOOP_DURATION_S
        loop = in_a_row = 0
        while time.time() < deadline:
            loop += 1
            path = posixpath.join(text_file_folder(), "text_file_%s_%d.txt" % (RUN_STAMP, loop))
            with allure_step("Loop %d: %s" % (loop, posixpath.basename(path))):
                loop_records, error = run_file_loop(page, session_name, path, loop)
            records += loop_records
            write_rows()
            if error:
                in_a_row += 1
                failures.append("loop %d: %s" % (loop, error))
                print("\n[rstudio-local] loop %d: %s" % (loop, error))
                if in_a_row >= MAX_FAILURES_IN_A_ROW:
                    print("\n[rstudio-local] %d loops failed in a row - stopping" % in_a_row)
                    break
            else:
                in_a_row = 0
                print("\n[rstudio-local] loop %d ok: %s" % (loop, describe_loop(loop_records)))
            if time.time() + LOOP_WAIT_S >= deadline:
                break  # the next loop would start after the duration is up
            page.wait_for_timeout(LOOP_WAIT_S * 1000)

        print("\n[rstudio-local] %d loop(s), %d failed; %s" % (loop, len(failures), step_summary(records)))
        assert not failures, "loops that failed:\n%s" % "\n".join(failures)

    finally:
        if records:
            write_rows()
        cleanup_and_verify_sessions(page, home_url, before_ids)
