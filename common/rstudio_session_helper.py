"""Workbench home page: sign in, the session list (rows, statuses, names)
and launching, opening and quitting sessions.

Flow: login -> session list -> New Session / open a row -> IDE ready
      ... -> session list -> tick rows -> Quit
"""
import time
from collections import namedtuple
from contextlib import contextmanager

from common import locators
from config.config import env
from common.evidence import save_screenshot
from common.session_naming_logic import AUTO_PERF_NAME_PREFIX, next_session_name

SESSION_TYPE = env("RSTUDIO_SESSION_TYPE", "RStudio Pro")

# elapsed_s runs from the click to the IDE's Console appearing;
# bytes_received counts response bodies in that window.
SessionLaunch = namedtuple("SessionLaunch", ["elapsed_s", "bytes_received", "session_name"])

# Session list statuses that count as a running session: idle (Active) or
# running code (Executing).
ACTIVE_STATUSES = ("Active", "Executing")

# The session table re-renders shortly after "New Session" appears.
SESSION_LIST_SETTLE_MS = 1500


def goto_session_list(page, home_url, settle_ms=SESSION_LIST_SETTLE_MS, timeout_ms=30000):
    """Navigate `page` to the session list and wait until it has rendered
    (plus settle_ms, so rows read afterwards are complete)."""
    page.goto(home_url)
    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=timeout_ms)
    if settle_ms:
        page.wait_for_timeout(settle_ms)


@contextmanager
def _count_response_bytes(page):
    """Yield a one-item list holding the bytes of every response body `page`
    receives inside the block."""
    received = [0]

    def _on_response(response):
        try:
            received[0] += len(response.body())
        except Exception:
            pass  # some responses (websocket upgrades, 304s, ...) have no body

    page.on("response", _on_response)
    try:
        yield received
    finally:
        page.remove_listener("response", _on_response)


def _click_and_wait_for_ide(page, target, session_name, timeout_ms, step):
    """Click `target` and wait for the session IDE's Console. Returns a
    SessionLaunch timed from the click. Screenshots the IDE as
    <step>_<session name>.png (after the timing), or the page as
    <step>_failed_<session name>.png if the IDE does not show."""
    try:
        with _count_response_bytes(page) as received:
            started = time.time()
            target.click()
            page.get_by_text(locators.CONSOLE_TAB_TEXT, exact=True).first.wait_for(
                state="visible", timeout=timeout_ms
            )
            elapsed = time.time() - started
    except Exception:
        save_screenshot(page, "%s_failed_%s.png" % (step, session_name), always=True)
        raise
    save_screenshot(page, "%s_%s.png" % (step, session_name))
    return SessionLaunch(elapsed_s=elapsed, bytes_received=received[0], session_name=session_name)


def open_existing_session(page, home_url, session_name, timeout_ms=60000):
    """Open the existing (Active or Suspended) session `session_name` from the
    session list and wait for its IDE. Returns a SessionLaunch. Raises
    RuntimeError if no such session is listed."""
    goto_session_list(page, home_url, settle_ms=0)

    # Wait for the row: a just-launched session's row can render late.
    link = page.get_by_role("link", name=session_name, exact=True)
    try:
        link.first.wait_for(state="visible", timeout=10000)
    except Exception:
        raise RuntimeError(
            "no session named %r found on the session list - it must already "
            "exist and be joinable (Active or Suspended)" % session_name
        )
    return _click_and_wait_for_ide(page, link.first, session_name, timeout_ms, "session_opened")


def is_session_listed(page, home_url, session_name, timeout_ms=5000):
    """Whether a session named `session_name` is on the session list (in any
    state). Navigates `page` to the session list."""
    goto_session_list(page, home_url)
    link = page.get_by_role("link", name=session_name, exact=True)
    try:
        link.first.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return False
    return True


def launch_new_session(page, home_url, session_name=None, timeout_ms=60000):
    """Launch a new session via the New Session dialog and wait for its IDE
    (Launch replaces the page's content rather than opening a tab).

    session_name defaults to the next AUTO_PERF_SESSION_<N>; pass one
    explicitly when launching concurrently. Returns a SessionLaunch.
    """
    goto_session_list(page, home_url, settle_ms=0 if session_name else SESSION_LIST_SETTLE_MS)
    if session_name is None:
        session_name = next_session_name(page, AUTO_PERF_NAME_PREFIX)

    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.click()
    page.get_by_text(SESSION_TYPE, exact=True).first.click()
    page.get_by_role(locators.SESSION_NAME_FIELD_ROLE, name=locators.SESSION_NAME_FIELD_NAME).fill(session_name)
    launch = page.get_by_role("button", name=locators.LAUNCH_BUTTON)
    launch.wait_for(state="visible", timeout=10000)
    save_screenshot(page, "session_create_dialog_%s.png" % session_name)
    return _click_and_wait_for_ide(page, launch, session_name, timeout_ms, "session_launched")


def is_session_alive(page):
    """Whether `page`'s session IDE is still showing its Console tab."""
    return page.get_by_text(locators.CONSOLE_TAB_TEXT, exact=True).first.count() > 0


def load_all_session_rows(page, max_scrolls=60, poll_interval_ms=1000, first_row_timeout_ms=20000):
    """Scroll the session list (the window and any scrolling container) to
    the bottom until the row count stops growing, so long lists that render
    rows lazily have them all in the page. Returns the row count.

    A long list (e.g. 200 sessions) can take several seconds to show its
    first row, so it first waits up to first_row_timeout_ms for one (an
    empty list costs that wait).
    """
    try:
        page.locator(locators.SESSION_STATUS_CELL_SELECTOR).first.wait_for(
            state="attached", timeout=first_row_timeout_ms)
    except Exception:
        return 0  # no sessions listed
    count, stable = len(row_ids(page)), 0
    for _ in range(max_scrolls):
        page.evaluate("""() => {
            window.scrollTo(0, document.body.scrollHeight);
            for (const el of document.querySelectorAll('*')) {
                if (el.scrollHeight > el.clientHeight + 20 && /auto|scroll/.test(getComputedStyle(el).overflowY)) {
                    el.scrollTop = el.scrollHeight;
                }
            }
        }""")
        page.wait_for_timeout(poll_interval_ms)
        new_count = len(row_ids(page))
        stable = stable + 1 if new_count == count else 0
        count = new_count
        if stable >= 3:
            break
    page.evaluate("() => window.scrollTo(0, 0)")
    return count


def row_ids(page):
    """Stable row ids (data-testid="cell-status-<hash>") on the session list.

    Read in one atomic call, since rows can disappear mid-read during
    cleanup and a per-index read would hang.
    """
    return set(page.eval_on_selector_all(
        locators.SESSION_STATUS_CELL_SELECTOR, "els => els.map(el => el.getAttribute('data-testid'))"
    ))


def _row_words(page):
    """{row id: set of the words in that row} for the session list, read in
    one atomic call like row_ids(). A session's name is one of its row's
    words (our names have no spaces), also on failed-launch rows where it
    may not be a link."""
    pairs = page.eval_on_selector_all(
        locators.SESSION_STATUS_CELL_SELECTOR,
        """els => els.map(el => {
            const row = el.closest('tr');
            return [el.getAttribute('data-testid'), row ? row.innerText.split(/\\s+/) : []];
        })""",
    )
    return {row_id: set(words) for row_id, words in pairs}


def session_row_ids_by_name(page, session_names):
    """{name: {row id, ...}} for the rows on the session list named exactly
    one of `session_names` (a name can have several rows). `page` must show
    the session list."""
    names = set(session_names)
    ids_by_name = {}
    for row_id, words in _row_words(page).items():
        for name in words & names:
            ids_by_name.setdefault(name, set()).add(row_id)
    return ids_by_name


def session_row_ids_with_prefix(page, prefixes):
    """Row ids on the session list whose session name starts with one of
    `prefixes`. `page` must show the session list."""
    prefixes = tuple(prefixes)
    return {row_id for row_id, words in _row_words(page).items() if any(w.startswith(prefixes) for w in words)}


def session_status_counts(page):
    """{status: number of rows} for the session list (e.g. {"Active": 3,
    "Suspended": 1}). Read in one atomic call like row_ids(), since the list
    updates live. `page` must show the session list."""
    statuses = page.eval_on_selector_all(
        locators.SESSION_STATUS_CELL_SELECTOR, "els => els.map(el => el.innerText.trim())"
    )
    counts = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


def stable_session_status_counts(page, timeout_ms=15000, poll_interval_ms=500):
    """session_status_counts() once the session list has loaded: the rows
    load after "New Session" appears (slower under load), so a single read
    can see an empty or half-filled table. Re-reads every poll_interval_ms
    until two reads in a row are equal and non-empty; returns the last read
    after timeout_ms (e.g. {} for a user with no sessions)."""
    deadline = time.time() + timeout_ms / 1000.0
    previous = None
    while True:
        counts = session_status_counts(page)
        if counts and counts == previous and "" not in counts:
            return counts
        if time.time() >= deadline:
            return counts
        previous = counts
        page.wait_for_timeout(poll_interval_ms)


def get_active_session_count(page):
    """Number of running sessions: rows with status Active or Executing
    (ACTIVE_STATUSES). `page` must show the session list."""
    counts = stable_session_status_counts(page)
    return sum(counts.get(status, 0) for status in ACTIVE_STATUSES)


def _click_and_confirm(page, click_action, confirm_button_name, timeout_ms=5000, poll_interval_ms=200):
    """Run `click_action`, and if a new confirmation dialog opens within
    timeout_ms, click confirm_button_name inside it.

    Waits for the dialog count to grow, since the trigger and confirm
    buttons share a name. No-op if no dialog appears.
    """
    dialogs = page.locator(locators.DIALOG_SELECTOR)
    before_count = dialogs.count()
    click_action()

    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline and dialogs.count() <= before_count:
        page.wait_for_timeout(poll_interval_ms)

    if dialogs.count() > before_count:
        dialogs.last.get_by_role("button", name=confirm_button_name).click()


def _session_row(page, row_id):
    return page.locator(locators.SESSION_STATUS_CELL_BY_ID % row_id).locator("xpath=ancestor::tr[1]")


def quit_session(page, row_id):
    """Remove the row `row_id`: "Remove" for a failed-launch stub row, else
    Details > Force quit. Never uses "Quit All", which quits every session.
    """
    row = _session_row(page, row_id)

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
    """Check each row's checkbox and click the scoped "Quit" button once,
    confirming the dialog if one opens. Rows without a checkbox fall back to
    quit_session(); rows that have already gone are skipped.

    Returns the row ids that were checked - the caller confirms removal.
    """
    checked = set()
    for row_id in row_ids:
        row = _session_row(page, row_id)
        if row.count() == 0:
            continue  # already gone

        checkbox = row.get_by_role(locators.SESSION_ROW_CHECKBOX_ROLE, name=locators.SESSION_ROW_CHECKBOX_NAME)
        if checkbox.count() == 0:
            checkbox = row.locator(locators.SESSION_ROW_CHECKBOX_XPATH)
        if checkbox.count() == 0:
            # e.g. a stub/failed-launch row - quit it on its own
            print("[rstudio-local] cleanup: %s has no checkbox, quitting it on its own" % row_id)
            try:
                quit_session(page, row_id)
            except Exception:
                pass
            continue

        try:
            checkbox.first.check(timeout=5000)
            checked.add(row_id)
        except Exception as exc:
            # A styled checkbox can be covered by its label: click it directly
            try:
                checkbox.first.click(force=True, timeout=5000)
                checked.add(row_id)
            except Exception:
                # don't let one bad checkbox stall the batch
                print("[rstudio-local] cleanup: could not check the checkbox of %s: %s" % (row_id, exc))

    if checked:
        quit_button = page.locator(locators.BULK_QUIT_BUTTON)
        if quit_button.count() == 0:
            print("[rstudio-local] cleanup: Quit button not found - %d checked session(s) not bulk-quit" % len(checked))
            save_screenshot(page, "bulk_quit_button_missing", always=True)
            return set()
        _click_and_confirm(page, quit_button.first.click, locators.BULK_QUIT_CONFIRM_BUTTON, timeout_ms=3000)
        print("[rstudio-local] cleanup: checked %d session(s) and clicked Quit once: %s"
              % (len(checked), ", ".join(sorted(checked))))
    return checked


# --- Sign in -------------------------------------------------------------------


def login(page, base_url, username=None, password=None):
    """Sign `page` into base_url and return the session list URL.
    Credentials default to RSTUDIO_USER1/RSTUDIO_PASSWORD.
    """
    username = username or env("RSTUDIO_USER1", required=True)
    password = password or env("RSTUDIO_PASSWORD", required=True)

    sign_in_url = base_url if base_url.endswith("/auth-sign-in") else base_url + "/auth-sign-in"
    page.goto(sign_in_url)
    page.locator(locators.USERNAME_INPUT).fill(username)
    page.locator(locators.PASSWORD_INPUT).fill(password)
    page.get_by_role("button", name=locators.SIGN_IN_BUTTON).click()
    page.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.wait_for(state="visible", timeout=50000)
    page.wait_for_timeout(2000)  # the session table re-renders shortly after
    return page.url
