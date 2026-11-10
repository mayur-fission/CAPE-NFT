"""Inside a session IDE: working directory, files and folders (through
the console), the editor, and creating a project from an existing folder.
"""
import posixpath
import time
from collections import namedtuple

from common import locators
from common.rstudio_console_commands import RCommandError, r_string, read_console_value, run_console_command
from config.config import env

# Longest one setwd() may take. A network mount that is slow to answer (e.g.
# /mnt/svmroot under load) can need far more than the default; too short and
# the retry types setwd() again while R is still busy with the first one.
SETWD_TIMEOUT_MS = int(float(env("SETWD_TIMEOUT_S") or 10) * 1000)


def set_working_directory(page, path, timeout_ms=None):
    """Run setwd(path) in the console. Raises RuntimeError if it fails or
    times out.
    """
    try:
        run_console_command(page, 'setwd(%s)' % r_string(path), timeout_ms=timeout_ms or SETWD_TIMEOUT_MS)
    except RCommandError as exc:
        # R only says "cannot change working directory": add whether the
        # folder is there, and whether this user may open (search) it
        raise RCommandError("%s - %s" % (exc, _folder_access(page, path)))


def _folder_access(page, path, timeout_ms=30000):
    """"<path>: exists TRUE/FALSE, user <u> can open: ..., read: ..., write: ..."
    for a folder, or the nearest existing folder above it. Never raises."""
    r_dir = r_string(path)
    r_expr = ("{d <- %s; while (!dir.exists(d) && d != dirname(d)) d <- dirname(d); "
              "a <- function(m) file.access(d, m) == 0; "
              "paste0(if (d == %s) paste0(d, ' exists') else paste0(%s, ' does not exist; nearest existing ', d), "
              "', user ', Sys.info()[['user']], ' can open: ', a(1), ', read: ', a(4), ', write: ', a(2))}"
              % (r_dir, r_dir, r_dir))
    try:
        return read_console_value(page, r_expr, timeout_ms)
    except Exception as exc:
        return "could not check the folder: %s" % " ".join(str(exc).split())[:150]


def get_working_directory(page, timeout_ms=10000):
    """The console's getwd()."""
    return read_console_value(page, "getwd()", timeout_ms)


def verify_working_directory(page, expected_path):
    """Assert getwd() is `expected_path` (ignoring a trailing slash) and
    return it."""
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


def delete_folder(page, path, timeout_ms=120000):
    """Delete the folder `path` with everything in it via unlink(). Returns
    True if it is gone afterwards. Raises ValueError for an empty path or
    one that is "/", "." or "~" (or ends in "..").
    """
    clean = posixpath.normpath(path or "")
    if not path or clean in ("/", ".", "~") or posixpath.basename(clean) in ("", "..", "~"):
        raise ValueError("refusing to delete folder %r" % path)
    r_path = r_string(clean)
    r_expr = "{unlink(%s, recursive=TRUE); format(!dir.exists(%s))}" % (r_path, r_path)
    return read_console_value(page, r_expr, timeout_ms) == "TRUE"


def create_folder(page, path, timeout_ms=10000):
    """Create `path` (recursively) via dir.create(). Returns True if created,
    False if it already existed or couldn't be created.
    """
    r_expr = 'format(dir.create(%s, recursive=TRUE))' % r_string(path)
    return read_console_value(page, r_expr, timeout_ms) == "TRUE"


def close_all_editor_files(page, session_name, timeout_ms=30000):
    """Close every file open in the session's editor without saving.
    A session reopens the files left open by earlier runs (e.g. a Workbench
    job script), which can get in the way of what the test opens next.
    Errors are printed, not raised.
    """
    # documentCloseAll() only exists in newer RStudio versions; older ones
    # run the IDE's File > Close All command instead.
    r_expr = (
        "{if (exists('.rs.api.documentCloseAll')) {.rs.api.documentCloseAll(save = FALSE); 'documentCloseAll'} "
        "else if (exists('.rs.api.executeCommand')) {.rs.api.executeCommand('closeAllSourceDocs', quiet = TRUE); "
        "'closeAllSourceDocs'} else 'none'}"
    )
    try:
        method = read_console_value(page, r_expr, timeout_ms)
        if method == "none":
            print("\n[rstudio-local] session %s: this RStudio cannot close editor files from the console"
                  % session_name)
            return
        page.wait_for_timeout(1500)
        print("\n[rstudio-local] session %s: closed the files open in the editor (%s)" % (session_name, method))
    except Exception as exc:
        print("\n[rstudio-local] session %s: could not close the editor files: %s" % (session_name, exc))


def _why_not_writable(page, folder, timeout_ms=30000):
    """Why a folder can't be made in `folder`: the nearest level of it that
    exists, and whether this user can write there. Never raises."""
    r_dir = r_string(folder)
    r_expr = ("{d <- %s; while (!dir.exists(d) && d != dirname(d)) d <- dirname(d); "
              "if (d == %s) paste0(d, ' exists, writable by ', Sys.info()[['user']], ': ', file.access(d, 2) == 0) "
              "else paste0(%s, ' does not exist in this session; nearest existing folder ', d, "
              "', writable by ', Sys.info()[['user']], ': ', file.access(d, 2) == 0)}") % (r_dir, r_dir, r_dir)
    try:
        return read_console_value(page, r_expr, timeout_ms)
    except Exception as exc:
        return "could not check why: %s" % " ".join(str(exc).split())[:150]


def recreate_session_folder(page, working_dir, session_name, timeout_ms=30000):
    """Delete `working_dir`/`session_name` with everything in it (data left
    by an earlier run of the session) and create it again, empty. Returns
    the folder path; raises RuntimeError if it could not be recreated.
    """
    if not working_dir or not session_name or "/" in session_name or session_name in (".", ".."):
        raise ValueError("refusing to recreate folder %r in %r" % (session_name, working_dir))
    path = posixpath.join(working_dir, session_name)
    r_path = r_string(path)
    r_expr = "{unlink(%s, recursive=TRUE); format(dir.create(%s, recursive=TRUE))}" % (r_path, r_path)
    if read_console_value(page, r_expr, timeout_ms) != "TRUE":
        raise RuntimeError("could not remove and recreate %s: %s" % (path, _why_not_writable(page, working_dir)))
    print("\n[rstudio-local] session %s: removed earlier test data, %s is empty" % (session_name, path))
    return path


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
