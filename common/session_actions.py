"""Single steps with this project's .env defaults (credentials,
RSTUDIO_WORKING_DIR), acting on whichever session `page` shows. Also passes
through the console/file helpers tests use, so they import from one place.

Flow: login (+ note the sessions already listed / reserve names) -> launch
      session (with retries) -> setwd
"""
from config.config import env
from common.evidence import save_screenshot
from common.rstudio_console_commands import (  # noqa: F401 - re-exported
    get_console_output,
    is_console_command_done,
    run_console_command,
    submit_console_command,
    wait_for_console_command,
)
from common.rstudio_session_helper import (
    get_active_session_count,
    goto_session_list,
    is_session_alive,
    launch_new_session,
)
from common.rstudio_session_helper import open_existing_session  # noqa: F401 - re-exported
from common.rstudio_files import close_all_editor_files, recreate_session_folder
from common.rstudio_session_helper import login as _login
from common.session_retry import wait_for_login_slot
from common.rstudio_files import set_working_directory as _set_working_directory
from common.step_log import log_step
from common.session_retry import retry
from common.rstudio_session_helper import load_all_session_rows
from common.rstudio_session_helper import row_ids
from common.session_naming_logic import next_session_names

WORKING_DIR = env("RSTUDIO_WORKING_DIR")


def login_to_posit_workbench(page, user=1):
    """Log into RSTUDIO_BASE_URL as `user` and return the session list URL.
    Waits first so logins from every test process and thread start at least
    LOGIN_MIN_INTERVAL_S apart (see wait_for_login_slot()). Screenshots the
    page after signing in as login_<username>.png.

    user=1 uses RSTUDIO_USER1/RSTUDIO_PASSWORD; user=N uses
    RSTUDIO_USER<N>/RSTUDIO_PASSWORD<N>.
    """
    base_url = env("RSTUDIO_BASE_URL", required=True).rstrip("/")
    password_suffix = "" if user == 1 else str(user)
    username = env("RSTUDIO_USER%s" % user, required=True)
    password = env("RSTUDIO_PASSWORD%s" % password_suffix, required=True)
    wait_for_login_slot()
    log_step("Logging in to Posit Workbench as %s", username)
    home_url = _login(page, base_url, username=username, password=password)
    log_step("Login successful: %s is signed in to Posit Workbench", username)
    save_screenshot(page, "login_%s.png" % username)
    return home_url


def print_active_session_count(page, home_url, label=None):
    """Go to the session list, print the Active session count (tagged with
    `label`) and return it."""
    goto_session_list(page, home_url, settle_ms=0)
    count = get_active_session_count(page)
    print("\n[rstudio-local] active sessions%s: %d" % (" after %s" % label if label else "", count))
    return count


def launch_session(page, home_url, session_name=None):
    """Launch a new session and wait for its IDE. Returns a SessionLaunch.
    session_name defaults to the next AUTO_PERF_SESSION_<N>."""
    return launch_new_session(page, home_url, session_name=session_name)


def launch_session_with_retries(page, home_url, session_name, attempts=3, label=None):
    """launch_session() with up to `attempts` tries and backoff, also
    retrying if the IDE isn't alive afterwards. `label` prefixes log lines.
    Returns the SessionLaunch; raises the last error if every attempt fails.
    """
    def _attempt(_):
        launch = launch_session(page, home_url, session_name=session_name)
        if not is_session_alive(page):
            raise AssertionError("session %s is not active after launch" % session_name)
        return launch

    what = "%s: session %s" % (label, session_name) if label else "session %s" % session_name
    return retry(_attempt, what, attempts)[0]


def set_session_working_directory(page, path=None):
    """setwd(path), path defaulting to RSTUDIO_WORKING_DIR. Returns False
    (no-op) if neither is set, else True."""
    path = path or WORKING_DIR
    if not path:
        return False
    _set_working_directory(page, path)
    return True


# --- Login steps that also note the sessions already listed --------------------


def login_and_snapshot(page, user=1):
    """Log `page` in as `user` and record the sessions already listed, so
    cleanup_and_verify_sessions() can quit only the ones the test creates.
    Returns (home_url, before_ids).
    """
    home_url = login_to_posit_workbench(page, user=user)
    load_all_session_rows(page)
    return home_url, row_ids(page)


def login_and_plan_session_names(page, prefix, count, user=1):
    """login_and_snapshot(), plus the next `count` free <prefix><N> names,
    reserved from one scan of the session list. Returns (home_url,
    before_ids, session_names).
    """
    home_url, before_ids = login_and_snapshot(page, user=user)
    return home_url, before_ids, next_session_names(page, prefix, count, home_url=home_url)
