"""Posit Workbench browser automation: sign-in, working directory, folder
and file management, and creating a project from an existing directory.

Console commands live in rstudio_console_commands.py, text files in
rstudio_text_operations.py, and session launch/quit in
rstudio_session_helper.py.
"""
import time
from collections import namedtuple

from common import locators
from common.config import env
from common.rstudio_console_commands import r_string, read_console_value, run_console_command


def login(page, base_url, username=None, password=None):
    """Sign `page` into base_url and return the session list URL.
    Credentials default to RSTUDIO_USER1/RSTUDIO_PASSWORD.
    """
    username = username or env("RSTUDIO_USER1", required=True)
    password = password or env("RSTUDIO_PASSWORD", required=True)

    sign_in_url = base_url if base_url.endswith("/auth-sign-in") else base_url + "/auth-sign-in"
    page.goto(sign_in_url)
    page.get_by_label(locators.USERNAME_LABEL).fill(username)
    page.get_by_label(locators.PASSWORD_LABEL).fill(password)
    page.get_by_role("button", name=locators.SIGN_IN_BUTTON).click()
    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=50000)
    page.wait_for_timeout(2000)  # the session table re-renders shortly after
    return page.url


def set_working_directory(page, path, timeout_ms=10000):
    """Run setwd(path) in the console. Raises RuntimeError if it fails or
    times out.
    """
    run_console_command(page, 'setwd(%s)' % r_string(path), timeout_ms=timeout_ms)


def get_working_directory(page, timeout_ms=10000):
    return read_console_value(page, "getwd()", timeout_ms)


def verify_working_directory(page, expected_path):
    actual_path = get_working_directory(page)

    def _normalize(path):
        return path if path == "/" else path.rstrip("/")

    assert _normalize(actual_path) == _normalize(expected_path), (
        "working directory is %r, expected %r" % (actual_path, expected_path)
    )
    return actual_path


def delete_file(page, path, timeout_ms=10000):
    """Delete `path` via file.remove(). Returns True if it was removed."""
    return read_console_value(page, 'format(file.remove(%s))' % r_string(path), timeout_ms) == "TRUE"


def create_folder(page, path, timeout_ms=10000):
    """Create `path` (recursively) via dir.create(). Returns True if created,
    False if it already existed or couldn't be created.
    """
    r_expr = 'format(dir.create(%s, recursive=TRUE))' % r_string(path)
    return read_console_value(page, r_expr, timeout_ms) == "TRUE"


def create_folders(page, paths, timeout_ms=10000):
    """create_folder() for each path. Returns one bool per path."""
    return [create_folder(page, path, timeout_ms=timeout_ms) for path in paths]


ProjectCreation = namedtuple("ProjectCreation", ["elapsed_s", "working_directory"])


def create_project_from_existing_directory(page, timeout_ms=45000):
    """File > New Project > Existing Directory > Create Project, using the
    console's current working directory (the wizard pre-fills it).

    Answers "Don't Save" if a Save Workspace dialog appears, and waits out
    the full IDE reload that Create Project triggers.

    Returns ProjectCreation(elapsed_s, working_directory), where elapsed_s
    runs from clicking Create Project to the Console reappearing.
    """
    page.locator(locators.FILE_MENU_SELECTOR).click()
    page.get_by_text(locators.NEW_PROJECT_MENU_ITEM_TEXT, exact=True).click()

    dont_save = page.get_by_role("button", name=locators.SAVE_WORKSPACE_DONT_SAVE_BUTTON)
    if dont_save.count() > 0:
        dont_save.click()

    page.get_by_text(locators.EXISTING_DIRECTORY_OPTION_TEXT, exact=True).first.wait_for(
        state="visible", timeout=10000
    )
    page.get_by_text(locators.EXISTING_DIRECTORY_OPTION_TEXT, exact=True).click()

    create_button = page.get_by_role("button", name=locators.CREATE_PROJECT_BUTTON)
    create_button.wait_for(state="visible", timeout=10000)

    started = time.time()
    with page.expect_navigation(timeout=timeout_ms):
        create_button.click()

    page.get_by_text(locators.CONSOLE_TAB_TEXT, exact=True).first.wait_for(state="visible", timeout=timeout_ms)
    elapsed = time.time() - started

    # Panes are still assembling for a moment after "Console" appears.
    page.wait_for_timeout(2000)
    working_directory = get_working_directory(page, timeout_ms=timeout_ms)

    return ProjectCreation(elapsed_s=elapsed, working_directory=working_directory)
