"""Housekeeping: delete the files and folders other tests leave on the
server, through the IDE's Files pane (common/files_pane.py).

One session (CLEANUP_SESSION_<N>) is launched, and each cleanup folder is
searched (list.files) for names the tests use:
  - in every output folder: per-session folders named after a session
    (every session_naming_logic prefix, AUTO_API_SESSION_, with or without
    a B<n>_/N<n>_ browser/node prefix) and the scripts' pmx_sim_output*
    folders
  - in <RSTUDIO_WORKING_DIR>/auto_test: the text file tests' text_file_*.txt
    and Text_<N> files
The matches are ticked in the Files pane, deleted with Delete > Yes, and
checked to be gone on the server. The session is then force-quit; if nothing
was left over, it is quit normally. Passes when there is nothing to delete.

Folders searched: CLEANUP_FOLDERS (comma-separated) or, unset, every output
folder setting in .env (RSTUDIO_WORKING_DIR, OUTPUT_DIR_*, ...). Extra name
patterns (R regexes, comma-separated) for those folders: CLEANUP_EXTRA_PATTERNS.
CLEANUP_PATTERNS replaces all patterns (and skips auto_test), e.g.
CLEANUP_PATTERNS=BACKGROUND_JOB_SESSION_ deletes only those folders.

Careful: it also deletes matching data that a test running at the same time
as the same user is still using.

    pytest tests/test_cleanup_leftover_testdata.py -v -m rstudio_local
"""
import os
import posixpath

import pytest

from common.evidence import save_screenshot
from common.files_pane import delete_in_files_pane, find_matching_names
from common.launch_already_created_sessions import API_SESSION_NAME_PREFIX
from common.perf_report import allure_step
from common.rstudio_console_commands import wait_for_console_ready
from common.rstudio_session_helper import goto_session_list, load_all_session_rows, quit_session, session_row_ids_by_name
from common.rstudio_text_operations import TEXT_FILE_SAVE_FOLDER
from common.session_actions import WORKING_DIR, launch_session_with_retries, login_and_plan_session_names
from common.session_cleanup import cleanup_multiple_sessions
from common.session_naming_logic import ALL_SESSION_NAME_PREFIXES, CLEANUP_SESSION_PREFIX
from config.config import env, env_list

pytestmark = pytest.mark.rstudio_local

USER = int(env("CLEANUP_USER", "1"))
# Every .env setting that names a folder tests write their output in
_OUTPUT_DIR_SETTINGS = ("RSTUDIO_WORKING_DIR", "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR",
                        "OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                        "OUTPUT_DIR_HIGH_THROUGHPUT", "BACKGROUND_JOB_OUTPUT_DIR")
OUTPUT_FOLDERS = env_list("CLEANUP_FOLDERS") or list(dict.fromkeys(
    os.environ[name].rstrip("/") or "/" for name in _OUTPUT_DIR_SETTINGS if os.environ.get(name)))
# Per-session folders (<output dir>/<session name>, optionally B1_/N1_
# prefixed) and the scripts' own output folders
SESSION_FOLDER_PATTERN = "^([BN][0-9]+_)*(%s)" % "|".join(ALL_SESSION_NAME_PREFIXES + (API_SESSION_NAME_PREFIX,))
OUTPUT_PATTERNS = [SESSION_FOLDER_PATTERN, "^pmx_sim_output"] + env_list("CLEANUP_EXTRA_PATTERNS", [])
# Set: only these patterns, only in the output folders (no auto_test search)
ONLY_PATTERNS = env_list("CLEANUP_PATTERNS")
# The text file tests' files (text_file_crud.py, rstudio_text_operations.py)
TEXT_FILE_PATTERN = "^(text_file_.*\\.txt|Text_[0-9]+(\\.txt)?)$"
DELETE_TIMEOUT_MS = int(env("CLEANUP_DELETE_TIMEOUT_MS", "300000"))


def _cleanup_targets():
    """[(folder, R regex), ...] to search, one regex per folder."""
    patterns = ONLY_PATTERNS or OUTPUT_PATTERNS
    targets = [(folder, "|".join("(%s)" % p for p in patterns)) for folder in OUTPUT_FOLDERS]
    if WORKING_DIR and not ONLY_PATTERNS:
        targets.append((posixpath.join(WORKING_DIR, TEXT_FILE_SAVE_FOLDER), TEXT_FILE_PATTERN))
    return targets


def _quit_cleanup_session(page, home_url, session_name, force):
    """Quit the cleanup session from the session list: Details > Force quit
    with `force`, else the normal (bulk) Quit. Asserts it is gone."""
    goto_session_list(page, home_url, settle_ms=2000)
    load_all_session_rows(page)
    ids = session_row_ids_by_name(page, [session_name]).get(session_name, set())
    print("\n[rstudio-local] %s session %s" % ("force-quitting" if force else "quitting", session_name))
    if force:
        for row_id in ids:
            try:
                quit_session(page, row_id)
            except Exception as exc:
                print("[rstudio-local] force quit of %s failed: %s" % (session_name, exc))
    # waits for the rows to go; quits (then force-quits) any still listed
    cleanup_multiple_sessions(page, ids)
    goto_session_list(page, home_url, settle_ms=2000)
    load_all_session_rows(page)
    left = session_row_ids_by_name(page, [session_name])
    if left:
        save_screenshot(page, "cleanup_session_not_quit.png", always=True)
    assert not left, "session %s is still listed after quitting it" % session_name


def test_cleanup_leftover_testdata(page):
    """Every leftover test file and folder found is deleted through the
    Files pane.

    Flow: login -> launch CLEANUP_SESSION_<N> -> per folder: search for the
    test naming patterns -> Files pane to the folder -> tick the matches ->
    Delete -> Yes -> check they are gone -> force-quit the session (quit
    it if nothing was found)
    """
    if not OUTPUT_FOLDERS and not WORKING_DIR:
        pytest.skip("no folder to clean - set CLEANUP_FOLDERS or RSTUDIO_WORKING_DIR in .env")
    home_url, _, (session_name,) = login_and_plan_session_names(page, CLEANUP_SESSION_PREFIX, 1, user=USER)
    found, deleted, left = {}, [], []
    try:
        with allure_step("Launch session %s" % session_name):
            launch_session_with_retries(page, home_url, session_name)
            wait_for_console_ready(page)

        for folder, pattern in _cleanup_targets():
            with allure_step("Search %s" % folder):
                names = find_matching_names(page, folder, pattern)
            if not names:
                print("\n[rstudio-local] %s: no leftover test data" % folder)
                continue
            found[folder] = names
            print("\n[rstudio-local] %s: %d leftover item(s): %s" % (folder, len(names), ", ".join(names)))
            with allure_step("Delete %d item(s) in %s" % (len(names), folder)):
                gone, still_there = delete_in_files_pane(page, folder, names, timeout_ms=DELETE_TIMEOUT_MS)
            deleted += [posixpath.join(folder, name) for name in gone]
            left += [posixpath.join(folder, name) for name in still_there]
            print("[rstudio-local] %s: deleted %d, %d still there" % (folder, len(gone), len(still_there)))

        if not found:
            print("\n[rstudio-local] no leftover test data in %s" % ", ".join(f for f, _ in _cleanup_targets()))
        else:
            print("\n[rstudio-local] deleted %d leftover item(s)%s"
                  % (len(deleted), "; could not delete: %s" % ", ".join(left) if left else ""))
        if left:
            save_screenshot(page, "cleanup_leftovers_not_deleted.png", always=True)
        assert not left, "leftover test data that could not be deleted:\n%s" % "\n".join(left)
    finally:
        with allure_step("Quit session %s" % session_name):
            _quit_cleanup_session(page, home_url, session_name, force=bool(found))
