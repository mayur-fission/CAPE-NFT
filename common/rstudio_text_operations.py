"""Text files in the editor.

Flow: File > New File > Text File -> type -> Ctrl+S -> Save File dialog
      edit: type at the end of the open tab -> Ctrl+S
      open: file.edit() -> the editor shows the file
      delete: file.remove() -> the file is gone
"""
import re
import time
from collections import namedtuple

from common import locators
from common.rstudio_console_commands import r_string, read_console_value, read_text_file, submit_console_command

AUTO_TEXT_FILE_PREFIX = "Text_"
_AUTO_TEXT_FILE_RE = re.compile(re.escape(AUTO_TEXT_FILE_PREFIX) + r"(\d+)")

DEFAULT_TEXT_FILE_CONTENT = "This is text entered by playwright"
TEXT_FILE_SAVE_FOLDER = "auto_test"

_ACE_TEXT_INPUT_SELECTOR = ".ace_text-input"

# Ctrl+S retries if the Save File dialog doesn't appear (see save_file()).
_SAVE_DIALOG_ATTEMPTS = 3

TextFileCreation = namedtuple("TextFileCreation", ["elapsed_s", "file_name", "folder"])


def _next_auto_text_file_name(page):
    """Next Text_<N> name, based on the files shown in the Save File dialog's
    current folder.
    """
    highest = 0
    body_text = page.locator("body").inner_text()
    for match in _AUTO_TEXT_FILE_RE.finditer(body_text):
        highest = max(highest, int(match.group(1)))
    return "%s%d" % (AUTO_TEXT_FILE_PREFIX, highest + 1)


def _active_editor_input(page, timeout_ms):
    """Locator for the active source tab's editor input, or None if none
    becomes visible within timeout_ms.

    Re-scanned on every call: every open tab stays in the DOM and their order
    can change, so a cached index goes stale.
    """
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        idx = page.evaluate(locators.ACTIVE_EDITOR_INPUT_INDEX_JS)
        if idx >= 0:
            return page.locator(_ACE_TEXT_INPUT_SELECTOR).nth(idx)
        page.wait_for_timeout(300)
    return None


def open_new_text_tab(page, timeout_ms=30000):
    """File > New File > Text File. Raises RuntimeError if the new tab's
    editor never becomes visible.
    """
    page.locator(locators.FILE_MENU_SELECTOR).click()
    page.locator(locators.NEW_FILE_MENU_ITEM_SELECTOR).wait_for(state="visible", timeout=10000)
    page.locator(locators.NEW_FILE_MENU_ITEM_SELECTOR).hover()

    text_file_item = page.get_by_text(locators.TEXT_FILE_MENU_ITEM_TEXT, exact=True)
    text_file_item.wait_for(state="visible", timeout=5000)
    text_file_item.click()

    if _active_editor_input(page, timeout_ms) is None:
        raise RuntimeError(
            "the new Text File tab's editor never became visible within %dms" % timeout_ms
        )


def type_in_active_tab(page, content, timeout_ms=10000):
    """Type `content` into the active source tab."""
    editor_input = _active_editor_input(page, timeout_ms)
    if editor_input is None:
        raise RuntimeError("no active editor tab found within %dms" % timeout_ms)
    editor_input.click(force=True)
    page.keyboard.type(content)




def _save_dialog_scope(page, name_field):
    """The Save File dialog if it can be found, else the whole page (callers
    take the last match, since dialogs are appended after the Files pane).
    """
    dialog = page.locator("[role='dialog']").filter(has=name_field)
    return dialog.last if dialog.count() > 0 else page


def save_file(page, folder=None, file_name=None, timeout_ms=30000):
    """Save the active tab (Ctrl+S) into `folder` (default
    TEXT_FILE_SAVE_FOLDER) as `file_name` (default: next Text_<N>).

    Retries Ctrl+S when the dialog doesn't appear, and double-clicks the
    folder (a single click only selects it).

    Returns TextFileCreation(elapsed_s, file_name, folder), where elapsed_s
    runs from clicking Save to the dialog closing.
    """
    folder = folder or TEXT_FILE_SAVE_FOLDER
    name_field = page.locator(locators.SAVE_FILE_NAME_INPUT_SELECTOR)
    last_exc = None
    for attempt in range(_SAVE_DIALOG_ATTEMPTS):
        refreshed_input = _active_editor_input(page, 5000)
        if refreshed_input is None:
            raise RuntimeError("no active editor tab found to save")
        refreshed_input.click(force=True)
        page.keyboard.press("Control+s")
        try:
            name_field.wait_for(state="visible", timeout=8000)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc

    _save_dialog_scope(page, name_field).get_by_text(folder, exact=True).last.dblclick()
    page.wait_for_timeout(1000)  # the dialog's listing re-renders for the new folder

    if file_name is None:
        file_name = _next_auto_text_file_name(page)
    name_field.fill(file_name)

    started = time.time()
    page.get_by_role("button", name=locators.SAVE_FILE_SAVE_BUTTON, exact=True).click()
    name_field.wait_for(state="hidden", timeout=timeout_ms)
    elapsed = time.time() - started

    return TextFileCreation(elapsed_s=elapsed, file_name=file_name, folder=folder)


def create_text_file(page, content=None, folder=None, file_name=None, timeout_ms=30000):
    """Open a new text tab, type `content` (default DEFAULT_TEXT_FILE_CONTENT)
    and save it with save_file(). Returns the TextFileCreation from save_file().
    """
    open_new_text_tab(page, timeout_ms=timeout_ms)
    type_in_active_tab(page, content or DEFAULT_TEXT_FILE_CONTENT, timeout_ms=timeout_ms)
    return save_file(page, folder=folder, file_name=file_name, timeout_ms=timeout_ms)


def _wait_until(page, check, what, timeout_ms, poll_ms=500):
    """Poll `check()` until it is true; raises RuntimeError naming `what`
    after timeout_ms."""
    deadline = time.time() + timeout_ms / 1000.0
    while True:
        if check():
            return
        if time.time() >= deadline:
            raise RuntimeError("%s within %dms" % (what, timeout_ms))
        page.wait_for_timeout(poll_ms)


def append_and_save_active_tab(page, content, path, timeout_ms=30000):
    """Edit the active tab - already saved as `path` on the server: go to its
    end, type a new line with `content` and save it (Ctrl+S, no dialog as
    the file has a name). Returns the seconds from Ctrl+S until the file on
    the server ends with `content`.
    """
    editor_input = _active_editor_input(page, timeout_ms)
    if editor_input is None:
        raise RuntimeError("no active editor tab found to edit")
    editor_input.click(force=True)
    page.keyboard.press("Control+End")
    page.keyboard.type("\n" + content)
    started = time.time()
    page.keyboard.press("Control+s")
    _wait_until(page, lambda: (read_text_file(page, path) or "").rstrip().endswith(content),
                "%s was not saved with the edit" % path, timeout_ms)
    return time.time() - started


def open_file_in_editor(page, path, timeout_ms=30000):
    """Open the file `path` in the editor (file.edit()) and wait until it is
    the active, visible editor tab. Returns (elapsed_s, lines): the seconds
    from submitting file.edit() until then, and the lines the editor shows.
    """
    started = time.time()
    submit_console_command(page, "file.edit(%s)" % r_string(path))
    same_file = ("isTRUE(normalizePath(.rs.api.getSourceEditorContext()$path, mustWork = FALSE) == "
                 "normalizePath(%s, mustWork = FALSE))" % r_string(path))
    _wait_until(page, lambda: _active_editor_input(page, 1000) is not None
                and read_console_value(page, same_file, 10000) == "TRUE",
                "%s did not open in the editor" % path, timeout_ms)
    elapsed = time.time() - started
    # one line per editor line, joined with a separator the console keeps on one line
    shown = read_console_value(
        page, "paste(.rs.api.getSourceEditorContext()$contents, collapse = '%s')" % _LINE_SEPARATOR, timeout_ms)
    return elapsed, shown.split(_LINE_SEPARATOR)


_LINE_SEPARATOR = "@@LINE@@"


def delete_file(page, path, timeout_ms=30000):
    """Delete the file `path` on the server (file.remove()) and check it is
    gone. Returns the seconds taken; raises RuntimeError if it still exists.
    """
    started = time.time()
    removed = read_console_value(page, "file.remove(%s)" % r_string(path), timeout_ms)
    if removed != "TRUE":
        raise RuntimeError("file.remove(%s) returned %s" % (path, removed))
    _wait_until(page, lambda: read_console_value(page, "file.exists(%s)" % r_string(path), timeout_ms) == "FALSE",
                "%s still exists after deleting it" % path, timeout_ms)
    return time.time() - started
