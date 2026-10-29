"""The IDE's Files pane: find files and folders by name, tick their
checkboxes and delete them with the pane's Delete button.

Flow: list.files(folder, pattern) in the console (search) -> Files pane to
      the folder -> tick each match's checkbox -> Delete -> Yes -> check
      on the server that they are gone
"""
import time

from common import locators
from common.evidence import save_screenshot
from common.rstudio_console_commands import r_string, read_console_value, run_console_command

_NAME_SEPARATOR = "@@NAME@@"


def find_matching_names(page, folder, pattern, timeout_ms=30000):
    """Sorted names of the files and folders directly in `folder` matching
    the R regex `pattern` (hidden ones excluded, as in the Files pane); []
    if the folder does not exist."""
    r_folder = r_string(folder)
    r_expr = ("if (dir.exists(%s)) paste(list.files(%s, pattern = %s, no.. = TRUE), collapse = '%s') else ''"
              % (r_folder, r_folder, r_string(pattern), _NAME_SEPARATOR))
    value = read_console_value(page, r_expr, timeout_ms)
    return sorted(name for name in value.split(_NAME_SEPARATOR) if name)


def open_folder_in_files_pane(page, folder, timeout_ms=30000):
    """Show `folder` in the Files pane (Files tab in front)."""
    run_console_command(page, "if (exists('.rs.api.filesPaneNavigate')) .rs.api.filesPaneNavigate(%s)"
                        % r_string(folder), timeout_ms=timeout_ms)
    tab = page.locator(locators.FILES_TAB_SELECTOR)
    if tab.count() > 0:
        tab.first.click()
    page.locator(locators.FILES_PANEL_SELECTOR).wait_for(state="visible", timeout=timeout_ms)


def _file_row(page, name):
    """The Files pane row named exactly `name`."""
    panel = page.locator(locators.FILES_PANEL_SELECTOR)
    return panel.locator(locators.FILES_ROW_SELECTOR).filter(has=page.get_by_text(name, exact=True)).last


def select_files(page, names, timeout_ms=30000):
    """Tick the checkbox of every Files pane row in `names`. Returns the
    names ticked; the rest (row or checkbox not found) are printed."""
    ticked = []
    for i, name in enumerate(names):
        row = _file_row(page, name)
        try:
            # the listing refreshes after navigating: give the first row time
            row.wait_for(state="attached", timeout=timeout_ms if i == 0 else 5000)
            checkbox = row.locator(locators.FILES_ROW_CHECKBOX_SELECTOR).first
            try:
                checkbox.check(timeout=5000)
            except Exception:
                checkbox.click(force=True, timeout=5000)  # a styled checkbox can be covered
            ticked.append(name)
        except Exception as exc:
            print("[rstudio-local] Files pane: could not tick %s: %s" % (name, " ".join(str(exc).split())[:200]))
    return ticked


def click_delete_and_confirm(page, timeout_ms=30000):
    """Click the Files pane's Delete button and Yes in its confirmation."""
    panel = page.locator(locators.FILES_PANEL_SELECTOR)
    delete = page.locator(locators.FILES_DELETE_BUTTON_SELECTOR)
    if delete.count() == 0:
        delete = panel.get_by_role("button", name=locators.FILES_DELETE_BUTTON)
    delete.first.click()

    yes = page.locator(locators.FILES_DELETE_CONFIRM_YES_SELECTOR)
    try:
        yes.first.wait_for(state="visible", timeout=10000)
    except Exception:
        yes = page.locator(locators.DIALOG_SELECTOR).get_by_role(
            "button", name=locators.FILES_DELETE_CONFIRM_YES_BUTTON, exact=True)
        yes.first.wait_for(state="visible", timeout=timeout_ms)
    save_screenshot(page, "files_pane_confirm_delete.png")
    yes.first.click()


def wait_until_deleted(page, folder, names, timeout_ms=120000, poll_ms=2000):
    """Wait until none of `names` exists in `folder` on the server. Returns
    the names still there after timeout_ms."""
    r_paths = "c(%s)" % ", ".join(r_string(folder.rstrip("/") + "/" + name) for name in names)
    deadline = time.time() + timeout_ms / 1000.0
    while True:
        left_flags = read_console_value(page, "paste(as.integer(file.exists(%s)), collapse = '')" % r_paths)
        left = [name for name, flag in zip(names, left_flags) if flag == "1"]
        if not left or time.time() >= deadline:
            return left
        page.wait_for_timeout(poll_ms)


def delete_in_files_pane(page, folder, names, timeout_ms=120000):
    """Show `folder` in the Files pane, tick `names`, click Delete and Yes,
    and wait for them to be gone. Returns (deleted, left): the names gone
    from the server and those still there (not ticked, or not deleted)."""
    open_folder_in_files_pane(page, folder)
    ticked = select_files(page, names)
    save_screenshot(page, "files_pane_selected_%d.png" % len(ticked), always=True)
    if ticked:
        click_delete_and_confirm(page)
    left = wait_until_deleted(page, folder, names, timeout_ms=timeout_ms if ticked else 0)
    return [name for name in names if name not in left], left
