"""Single-step actions on a session, with this project's .env defaults
(credentials, RSTUDIO_WORKING_DIR). They act on whichever session IDE `page`
is showing. Also re-exports the console/file helpers tests and scenarios use,
so they have one module to import from.

Multi-step flows live in session_scenarios.py, session_batch.py and
session_concurrent.py.
"""
from common.config import env
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
from common.rstudio_workbench import create_folder, delete_file  # noqa: F401 - re-exported
from common.rstudio_workbench import login as _login
from common.rstudio_workbench import set_working_directory as _set_working_directory
from common.session_retry import retry

WORKING_DIR = env("RSTUDIO_WORKING_DIR")


def login_to_posit_workbench(page, user=1):
    """Log into RSTUDIO_BASE_URL as `user` and return the session list URL.

    user=1 uses RSTUDIO_USER1/RSTUDIO_PASSWORD; user=N uses
    RSTUDIO_USER<N>/RSTUDIO_PASSWORD<N>.
    """
    base_url = env("RSTUDIO_BASE_URL", required=True).rstrip("/")
    password_suffix = "" if user == 1 else str(user)
    username = env("RSTUDIO_USER%s" % user, required=True)
    password = env("RSTUDIO_PASSWORD%s" % password_suffix, required=True)
    return _login(page, base_url, username=username, password=password)


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
