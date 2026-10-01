"""Single-step session actions: thin wrappers over the rstudio_* modules
that add this project's .env defaults. They act on whichever session IDE
`page` is showing.

Multi-step scenarios live in session_scenarios.py, session_batch.py and
session_concurrent.py.
"""
import time

from common.config import env
from common.rstudio_workbench import create_folder as _create_folder
from common.rstudio_workbench import create_folders as _create_folders
from common.rstudio_workbench import delete_file as _delete_file
from common.rstudio_workbench import login as _login
from common.rstudio_workbench import set_working_directory as _set_working_directory
from common.rstudio_workbench import verify_working_directory as _verify_working_directory

from common.rstudio_console_commands import check_file_exists as _check_file_exists
from common.rstudio_console_commands import get_console_output as _get_console_output
from common.rstudio_console_commands import get_file_content as _get_file_content
from common.rstudio_console_commands import get_file_count as _get_file_count
from common.rstudio_console_commands import get_file_list as _get_file_list
from common.rstudio_console_commands import get_file_size as _get_file_size
from common.rstudio_console_commands import get_folder_size as _get_folder_size
from common.rstudio_console_commands import verify_file_content as _verify_file_content
from common.rstudio_console_commands import run_console_command as _run_console_command
from common.rstudio_console_commands import submit_console_command as _submit_console_command
from common.rstudio_console_commands import is_console_command_done as _is_console_command_done
from common.rstudio_console_commands import wait_for_console_command as _wait_for_console_command
from common.rstudio_console_commands import run_r_script as _run_r_script

from common.rstudio_text_operations import open_new_text_tab as _open_new_text_tab
from common.rstudio_text_operations import type_in_active_tab as _type_in_active_tab
from common.rstudio_text_operations import switch_to_file_tab as _switch_to_file_tab
from common.rstudio_text_operations import save_file as _save_file

from common.rstudio_session_helper import get_active_session_count as _get_active_session_count
from common.rstudio_session_helper import is_session_alive as _is_session_alive
from common.rstudio_session_helper import open_existing_session as _open_existing_session
from common.rstudio_session_helper import launch_new_session as _launch_new_session

from common.session_retry import _retry_backoff_s

WORKING_DIR = env("RSTUDIO_WORKING_DIR")


def login_to_posit_workbench(page, user=1):
    """Log into RSTUDIO_BASE_URL and return the session list URL.

    user=1 uses RSTUDIO_USER1/RSTUDIO_PASSWORD; user=N uses
    RSTUDIO_USER<N>/RSTUDIO_PASSWORD<N>.
    """
    base_url = env("RSTUDIO_BASE_URL", required=True).rstrip("/")
    password_suffix = "" if user == 1 else str(user)
    username = env("RSTUDIO_USER%s" % user, required=True)
    password = env("RSTUDIO_PASSWORD%s" % password_suffix, required=True)
    return _login(page, base_url, username=username, password=password)


def get_active_session_count(page):
    """Number of Active sessions. Assumes `page` shows the session list."""
    return _get_active_session_count(page)


def print_active_session_count(page, home_url, label=None):
    """Go to the session list, print the Active session count (tagged with
    `label`) and return it.
    """
    page.goto(home_url)
    page.get_by_text("New Session", exact=True).first.wait_for(state="visible", timeout=30000)
    count = _get_active_session_count(page)
    if label:
        print("\n[rstudio-local] active sessions after %s: %d" % (label, count))
    else:
        print("\n[rstudio-local] active sessions: %d" % count)
    return count


def launch_session(page, home_url, session_name=None):
    """Launch one session and wait for its IDE. Returns a SessionLaunch.
    session_name defaults to the next AUTO_PERF_SESSION_<N>.
    """
    return _launch_new_session(page, home_url, session_name=session_name)


def launch_session_with_retries(page, home_url, session_name, attempts=3, label=None):
    """launch_session() with up to `attempts` tries and backoff, also
    retrying if the session isn't alive afterwards. `label` prefixes log
    lines. Raises the last error if every attempt fails.
    """
    prefix = "[rstudio-local] %s: " % label if label else "[rstudio-local] "
    last_exc = None
    for attempt in range(attempts):
        try:
            launch = launch_session(page, home_url, session_name=session_name)
            if not _is_session_alive(page):
                raise AssertionError("session %s is not active after launch" % session_name)
            if attempt:
                print("%ssession %s launched on attempt %d/%d"
                      % (prefix, session_name, attempt + 1, attempts))
            return launch
        except Exception as exc:
            last_exc = exc
            print("%ssession %s attempt %d/%d failed: %s"
                  % (prefix, session_name, attempt + 1, attempts, exc))
            if attempt + 1 < attempts:
                time.sleep(_retry_backoff_s(attempt))
    raise last_exc


def open_existing_session(page, home_url, session_name):
    """Open an existing (Active or Suspended) session. Returns a SessionLaunch."""
    return _open_existing_session(page, home_url, session_name)


def set_session_working_directory(page, path=None):
    """setwd(path), path defaulting to RSTUDIO_WORKING_DIR. Returns False
    (no-op) if neither is set, else True.
    """
    path = path or WORKING_DIR
    if not path:
        return False
    _set_working_directory(page, path)
    return True


def verify_session_working_directory(page, path=None):
    """Assert getwd() matches `path` (default RSTUDIO_WORKING_DIR) and return
    it. Returns None (no-op) if neither is set.
    """
    path = path or WORKING_DIR
    if not path:
        return None
    return _verify_working_directory(page, path)


def create_folder(page, path, timeout_ms=10000):
    """Create folder `path`. Returns True if created, False if it existed."""
    return _create_folder(page, path, timeout_ms=timeout_ms)


def create_folders(page, paths, timeout_ms=10000):
    """create_folder() for each path. Returns one bool per path."""
    return _create_folders(page, paths, timeout_ms=timeout_ms)


def open_new_text_tab(page, timeout_ms=30000):
    """Open a new, empty text file tab (File > New File > Text File)."""
    _open_new_text_tab(page, timeout_ms=timeout_ms)


def type_in_active_tab(page, content, timeout_ms=10000):
    """Type `content` into the active source tab."""
    _type_in_active_tab(page, content, timeout_ms=timeout_ms)


def switch_to_file_tab(page, file_name, timeout_ms=10000):
    """Switch to the open source tab named `file_name`."""
    _switch_to_file_tab(page, file_name, timeout_ms=timeout_ms)


def save_file(page, folder=None, file_name=None, timeout_ms=30000):
    """Save the active tab into `folder` as `file_name` (defaults:
    TEXT_FILE_SAVE_FOLDER, next Text_<N>). Returns a TextFileCreation.
    """
    kwargs = {"timeout_ms": timeout_ms}
    if folder is not None:
        kwargs["folder"] = folder
    if file_name is not None:
        kwargs["file_name"] = file_name
    return _save_file(page, **kwargs)


def check_file_exists(page, path, timeout_ms=10000):
    """Whether `path` exists."""
    return _check_file_exists(page, path, timeout_ms=timeout_ms)


def delete_file(page, path, timeout_ms=10000):
    """Delete `path`. Returns True if it was removed."""
    return _delete_file(page, path, timeout_ms=timeout_ms)


def get_session_file_list(page, path, recursive=False, timeout_ms=15000):
    """Names of the files in `path` ([] if empty or missing)."""
    return _get_file_list(page, path, recursive=recursive, timeout_ms=timeout_ms)


def get_session_file_content(page, path, timeout_ms=10000):
    """Text content of `path`. Raises RuntimeError if it doesn't exist."""
    return _get_file_content(page, path, timeout_ms=timeout_ms)


def verify_session_file_content(page, path, expected_content, timeout_ms=10000):
    """Assert `path` contains `expected_content` and return it."""
    return _verify_file_content(page, path, expected_content, timeout_ms=timeout_ms)


def get_session_file_count(page, path, recursive=False, timeout_ms=10000):
    """Number of files in `path` (0 if empty or missing)."""
    return _get_file_count(page, path, recursive=recursive, timeout_ms=timeout_ms)


def get_session_file_size(page, path, timeout_ms=10000):
    """Size of `path` in bytes, or None if it doesn't exist."""
    return _get_file_size(page, path, timeout_ms=timeout_ms)


def get_session_folder_size(page, path, recursive=True, timeout_ms=15000):
    """Total size in bytes of the files under `path`."""
    return _get_folder_size(page, path, recursive=recursive, timeout_ms=timeout_ms)


def run_console_command(page, command, timeout_ms=300000):
    """Run `command` in the console and return the elapsed seconds."""
    return _run_console_command(page, command, timeout_ms=timeout_ms)


def submit_console_command(page, command):
    """Submit `command` without waiting. Returns (marker, started)."""
    return _submit_console_command(page, command)


def is_console_command_done(page, marker):
    """Non-blocking check for whether `marker` has appeared."""
    return _is_console_command_done(page, marker)


def wait_for_console_command(page, marker, started, timeout_ms=300000):
    """Wait for `marker` and return seconds since `started`."""
    return _wait_for_console_command(page, marker, started, timeout_ms=timeout_ms)


def get_console_output(page):
    """Full text of the console output pane."""
    return _get_console_output(page)


def run_r_script(page, script_path, timeout_ms=300000):
    """source() `script_path` and return the elapsed seconds."""
    return _run_r_script(page, script_path, timeout_ms=timeout_ms)
