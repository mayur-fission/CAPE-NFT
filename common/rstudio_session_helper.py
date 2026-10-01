"""Session lifecycle automation: naming, launching, opening and quitting
sessions, and reading the session list. Workbench has no public REST API
for this, so it drives the UI.
"""
import re
import time
from collections import namedtuple

from common import locators
from common.config import env

SESSION_TYPE = env("RSTUDIO_SESSION_TYPE", "RStudio Pro")

SessionLaunch = namedtuple("SessionLaunch", ["elapsed_s", "bytes_received", "session_name"])

AUTO_PERF_NAME_PREFIX = "AUTO_PERF_SESSION_"

# Names the sessions launched by tests/test_rstudio_run_r_script_perf.py.
RUN_R_SESSION_NAME_PREFIX = "Run_R_session_"


def _highest_number_with_prefix(page, prefix):
    """Highest N among <prefix><N> names on the session list (0 if none)."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    highest = 0
    rows = page.locator(locators.SESSION_ROW_SELECTOR)
    for i in range(rows.count()):
        for match in pattern.finditer(rows.nth(i).inner_text()):
            highest = max(highest, int(match.group(1)))
    return highest


def next_session_name(page, prefix, home_url=None):
    """Next <prefix><N> name from the session list. Pass home_url to navigate
    there first.
    """
    if home_url:
        page.goto(home_url)
        page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=30000)
        page.wait_for_timeout(1500)  # the session table re-renders shortly after
    return "%s%d" % (prefix, _highest_number_with_prefix(page, prefix) + 1)


def next_session_names(page, prefix, count, home_url=None):
    """Reserve `count` consecutive <prefix><N> names from one scan, so
    concurrent browsers don't race for the same name.
    """
    if home_url:
        page.goto(home_url)
        page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=30000)
        page.wait_for_timeout(1500)
    start = _highest_number_with_prefix(page, prefix) + 1
    return ["%s%d" % (prefix, start + i) for i in range(count)]


def next_auto_perf_session_name(page, home_url=None):
    """next_session_name() with the AUTO_PERF_SESSION_ prefix."""
    return next_session_name(page, AUTO_PERF_NAME_PREFIX, home_url=home_url)


def next_auto_perf_session_names(page, count, home_url=None):
    """next_session_names() with the AUTO_PERF_SESSION_ prefix."""
    return next_session_names(page, AUTO_PERF_NAME_PREFIX, count, home_url=home_url)


def open_existing_session(page, home_url, session_name, timeout_ms=60000):
    """Open the existing session `session_name` from the session list and
    wait for its IDE. Returns a SessionLaunch.
    """
    page.goto(home_url)
    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=30000)

    # Wait for the row: a just-launched session's row can render late.
    link = page.get_by_role("link", name=session_name, exact=True)
    try:
        link.first.wait_for(state="visible", timeout=10000)
    except Exception:
        raise RuntimeError(
            "no session named %r found on the session list - it must already "
            "exist and be joinable (Active or Suspended)" % session_name
        )

    bytes_received = [0]

    def _on_response(response):
        try:
            bytes_received[0] += len(response.body())
        except Exception:
            pass  # some responses (websocket upgrades, 304s, ...) have no body

    page.on("response", _on_response)
    try:
        started = time.time()
        link.first.click()
        page.get_by_text(locators.CONSOLE_TAB_TEXT, exact=True).first.wait_for(state="visible", timeout=timeout_ms)
        elapsed = time.time() - started
    finally:
        page.remove_listener("response", _on_response)

    return SessionLaunch(elapsed_s=elapsed, bytes_received=bytes_received[0], session_name=session_name)


def launch_new_session(page, home_url, session_name=None):
    """Launch a new session via the New Session dialog and wait for its IDE.

    session_name defaults to the next AUTO_PERF_SESSION_<N>; pass one
    explicitly when launching concurrently.

    Returns SessionLaunch(elapsed_s, bytes_received, session_name), where
    elapsed_s runs from clicking Launch to the IDE appearing.
    """
    page.goto(home_url)
    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=30000)

    if session_name is None:
        page.wait_for_timeout(1500)  # the session table re-renders shortly after
        session_name = next_auto_perf_session_name(page)

    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.click()

    page.get_by_text(SESSION_TYPE, exact=True).first.click()
    page.get_by_role(locators.SESSION_NAME_FIELD_ROLE, name=locators.SESSION_NAME_FIELD_NAME).fill(session_name)
    launch = page.get_by_role("button", name=locators.LAUNCH_BUTTON)
    launch.wait_for(state="visible", timeout=10000)

    bytes_received = [0]

    def _on_response(response):
        try:
            bytes_received[0] += len(response.body())
        except Exception:
            pass  # some responses (websocket upgrades, 304s, ...) have no body

    page.on("response", _on_response)
    try:
        started = time.time()
        launch.click()

        # Launch swaps the current page's content to the new session's IDE
        # rather than opening a tab. "Console" only appears once that IDE
        # has rendered.
        page.get_by_text(locators.CONSOLE_TAB_TEXT, exact=True).first.wait_for(state="visible", timeout=60000)
        elapsed = time.time() - started
    finally:
        page.remove_listener("response", _on_response)

    return SessionLaunch(elapsed_s=elapsed, bytes_received=bytes_received[0], session_name=session_name)


def is_session_alive(page):
    """Whether `page`'s session IDE is still showing its Console tab."""
    return page.get_by_text(locators.CONSOLE_TAB_TEXT, exact=True).first.count() > 0


def row_ids(page):
    """Stable row ids (data-testid="cell-status-<hash>") on the session list.

    Read in one atomic call, since rows can disappear mid-read during
    cleanup and a per-index read would hang.
    """
    return set(page.eval_on_selector_all(
        locators.SESSION_STATUS_CELL_SELECTOR, "els => els.map(el => el.getAttribute('data-testid'))"
    ))


def get_active_session_count(page):
    """Number of rows on the session list with status Active. Assumes `page`
    is already showing the session list.
    """
    cells = page.locator(locators.SESSION_STATUS_CELL_SELECTOR)
    return sum(1 for i in range(cells.count()) if "Active" in cells.nth(i).inner_text())


def new_row_id_after(page, home_url, before_ids):
    """Return the one row id not in before_ids, or None if there isn't
    exactly one.
    """
    page.goto(home_url)
    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=30000)
    page.wait_for_timeout(1500)  # the session table re-renders shortly after
    new_ids = row_ids(page) - before_ids
    return new_ids.pop() if len(new_ids) == 1 else None


_DIALOG_SELECTOR = "[role='dialog'], [role='alertdialog']"


def _click_and_confirm(page, click_action, confirm_button_name, timeout_ms=5000, poll_interval_ms=200):
    """Run `click_action`, and if a new confirmation dialog opens within
    timeout_ms, click confirm_button_name inside it.

    Waits for the dialog count to grow, since the trigger and confirm
    buttons share a name. No-op if no dialog appears.
    """
    dialogs = page.locator(_DIALOG_SELECTOR)
    before_count = dialogs.count()
    click_action()

    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline and dialogs.count() <= before_count:
        page.wait_for_timeout(poll_interval_ms)

    if dialogs.count() > before_count:
        dialogs.last.get_by_role("button", name=confirm_button_name).click()


def quit_session(page, row_id):
    """Remove the row `row_id`: "Remove" for a failed-launch stub row, else
    Details > Force quit. Never uses "Quit All", which quits every session.
    """
    row = page.locator(locators.SESSION_STATUS_CELL_BY_ID % row_id).locator("xpath=ancestor::tr[1]")

    remove = row.get_by_text(locators.REMOVE_LINK_TEXT, exact=True)
    if remove.count() > 0:
        _click_and_confirm(page, remove.click, locators.REMOVE_LINK_TEXT)
        return

    row.get_by_text(locators.DETAILS_LINK_TEXT, exact=True).click()
    page.get_by_text(locators.SESSION_DETAILS_HEADING_TEXT, exact=True).wait_for(state="visible", timeout=10000)

    force_quit_button = page.get_by_role("button", name=locators.FORCE_QUIT_BUTTON)
    _click_and_confirm(page, force_quit_button.click, locators.FORCE_QUIT_BUTTON)

    # Workbench shows a one-off "Abnormal exit" banner after a forced quit
    dismiss = page.get_by_role("button", name=locators.DISMISS_BUTTON)
    if dismiss.count() > 0:
        dismiss.click()


def bulk_quit_sessions(page, row_ids):
    """Check each row's checkbox and click the scoped "Quit (N)" button once
    (no confirmation dialog). Rows without a checkbox fall back to
    quit_session(); rows that have already gone are skipped.

    Returns the row ids that were checked - the caller confirms removal.
    """
    checked = set()
    for row_id in row_ids:
        row = page.locator(locators.SESSION_STATUS_CELL_BY_ID % row_id).locator("xpath=ancestor::tr[1]")
        if row.count() == 0:
            continue  # already gone - tolerate, don't fail the batch

        checkbox = row.get_by_role(locators.SESSION_ROW_CHECKBOX_ROLE, name=locators.SESSION_ROW_CHECKBOX_NAME)
        if checkbox.count() == 0:
            # No checkbox on this row (e.g. a stub/failed-launch job row) -
            # fall back to the single-row path for this one row only.
            try:
                quit_session(page, row_id)
            except Exception:
                pass
            continue

        try:
            checkbox.first.check()
            checked.add(row_id)
        except Exception:
            pass  # skip this row, don't let one bad checkbox stall the batch

    if not checked:
        return checked

    quit_button = page.get_by_role("button", name=locators.BULK_QUIT_BUTTON_TEXT % len(checked), exact=True)
    if quit_button.count() > 0:
        quit_button.click()

    return checked
