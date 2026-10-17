"""Workbench Jobs pane: start, inspect and stop Workbench jobs (they run
outside the session).

Flow: open script in editor -> Workbench Jobs > Start Workbench Job -> Start
      ... -> Stop Job -> confirm
"""
import posixpath
import time

from common import locators
from common.rstudio_console_commands import r_string, run_console_command


def open_workbench_jobs_tab(page, timeout_ms=10000):
    """Click the Workbench Jobs tab next to the Console."""
    tab = page.locator(locators.WORKBENCH_JOBS_TAB_SELECTOR)
    tab.wait_for(state="visible", timeout=timeout_ms)
    dismiss_open_dialogs(page)
    tab.click()


def dismiss_open_dialogs(page, timeout_ms=5000):
    """Close any modal dialog left open in the IDE (its overlay blocks every
    click) with Escape, printing what it said. Returns the dialogs' texts.
    """
    dialogs = page.locator(locators.DIALOG_SELECTOR)
    glass = page.locator(locators.MODAL_GLASS_SELECTOR)
    texts = []
    for _ in range(3):
        visible = [dialogs.nth(i) for i in range(dialogs.count()) if dialogs.nth(i).is_visible()]
        glass_up = any(glass.nth(i).is_visible() for i in range(glass.count()))
        if not visible and not glass_up:
            break
        for dialog in visible:
            text = " ".join((dialog.get_attribute("aria-label") or "").split() + dialog.inner_text().split())
            texts.append(text)
            print("[rstudio-local] closing a dialog left open: %s" % text[:300])
        if not visible:
            print("[rstudio-local] a modal overlay with no dialog is blocking the IDE, pressing Escape")
        page.keyboard.press("Escape")
        page.wait_for_timeout(timeout_ms if not visible else 0)
        if visible:
            try:
                visible[-1].wait_for(state="hidden", timeout=timeout_ms)
            except Exception:
                pass
    return texts


def open_start_workbench_job_dialog(page, timeout_ms=10000, attempts=3):
    """Click Start Workbench Job in the Workbench Jobs pane and return the
    dialog locator. Retries the click: right after the pane opens, the button
    is visible but a click can be ignored. Dialogs left open by something
    else (their overlay intercepts the click) are closed first.
    """
    button = page.locator(locators.START_WORKBENCH_JOB_BUTTON_SELECTOR)
    dialog = page.locator("[role='dialog']").filter(has_text=locators.WORKBENCH_JOB_DIALOG_TITLE).last
    button.wait_for(state="visible", timeout=timeout_ms)
    for attempt in range(attempts):
        dismiss_open_dialogs(page)
        try:
            button.click(timeout=timeout_ms)
            dialog.wait_for(state="visible", timeout=timeout_ms)
            return dialog
        except Exception:
            if attempt + 1 == attempts:
                raise


def _check_script_path(dialog, script_path):
    """The R Script box is read-only and pre-filled from the active editor
    tab; check that it holds `script_path`. Paths under the home directory
    are shown as ~/..., so /home/<user>/x.R matches ~/x.R.
    """
    value = dialog.locator(locators.WORKBENCH_JOB_SCRIPT_INPUT_SELECTOR).input_value()
    under_home = (
        value.startswith("~/")
        and script_path.startswith("/home/")
        and script_path.split("/", 3)[3:] == [value[2:]]
    )
    if value != script_path and not under_home:
        raise RuntimeError("R Script box shows %r, expected %r" % (value, script_path))


def _job_names(page, job_name):
    """Locator for the name cell of every `job_name` entry in the Workbench
    Jobs pane, newest first. The pane lists all of the user's jobs, not just
    this session's. Scoped to the pane so it can't match the editor tab.
    """
    return page.locator(locators.WORKBENCH_JOBS_PANEL_SELECTOR).get_by_text(job_name, exact=True)


def _settled_job_count(page, job_name, timeout_ms=15000, stable_ms=3000, poll_interval_ms=500):
    """Number of `job_name` entries once the jobs list has stopped changing.
    The list loads a few seconds after the pane opens, so counting straight
    away can undercount.
    """
    deadline = time.time() + timeout_ms / 1000.0
    panel = page.locator(locators.WORKBENCH_JOBS_PANEL_SELECTOR)
    # Until the list loads the pane is empty, which would look "stable" at 0;
    # wait for any job's status to show (or the timeout, if there are none).
    while time.time() < deadline and not any(s in panel.inner_text() for s in locators.WORKBENCH_JOB_ANY_STATES):
        page.wait_for_timeout(poll_interval_ms)
    count, stable_since = _job_names(page, job_name).count(), time.time()
    while time.time() < deadline and time.time() - stable_since < stable_ms / 1000.0:
        page.wait_for_timeout(poll_interval_ms)
        current = _job_names(page, job_name).count()
        if current != count:
            count, stable_since = current, time.time()
    return count


def _wait_for_new_job(page, job_name, before_count, timeout_ms, poll_interval_ms=500):
    """Wait until there are more `job_name` entries than before_count."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if _job_names(page, job_name).count() > before_count:
            return
        page.wait_for_timeout(poll_interval_ms)
    raise RuntimeError("no new %r entry appeared in the Workbench Jobs pane within %dms" % (job_name, timeout_ms))


def get_job_status(page, job_name):
    """Status text of the newest `job_name` entry (e.g. "Running",
    "Succeeded 5:10 PM"), or None if there is none.
    """
    names = _job_names(page, job_name)
    if names.count() == 0:
        return None
    entry = names.first.locator("xpath=ancestor::table[1]")
    return " ".join(entry.inner_text().replace(job_name, "", 1).split())


def stop_workbench_job(page, job_name, timeout_ms=30000):
    """Stop the newest still-running `job_name` entry (Stop Job, then confirm
    Stop Job). The pane lists all of the user's jobs, so with several
    sessions the newest entry may already be stopped. Returns False if no
    `job_name` entry has a Stop Job button.
    """
    open_workbench_jobs_tab(page)
    _settled_job_count(page, job_name)

    def _stop_buttons():
        """Stop Job button of each running `job_name` entry, newest first."""
        names = _job_names(page, job_name)
        buttons = []
        for i in range(names.count()):
            button = names.nth(i).locator("xpath=ancestor::table[1]").get_by_role(
                "button", name=locators.WORKBENCH_JOB_STOP_BUTTON
            )
            if button.count() > 0:
                buttons.append(button)
        return buttons

    running = _stop_buttons()
    running_before = len(running)
    if running_before == 0:
        return False
    # Right after the pane loads a click can be ignored, so retry until the
    # confirmation appears.
    prompt = page.get_by_text(locators.WORKBENCH_JOB_STOP_CONFIRM_TEXT)
    for attempt in range(3):
        running[0].click()
        try:
            prompt.first.wait_for(state="visible", timeout=5000)
            break
        except Exception:
            if attempt == 2:
                raise

    # The confirmation box is appended last to the page, so its "Stop Job"
    # button is the last one with that name (entry buttons share it).
    page.get_by_role("button", name=locators.WORKBENCH_JOB_STOP_BUTTON, exact=True).last.click()
    prompt.first.wait_for(state="hidden", timeout=timeout_ms)

    deadline = time.time() + timeout_ms / 1000.0
    while len(_stop_buttons()) >= running_before:
        if time.time() > deadline:
            raise RuntimeError("Workbench job %r still running %dms after Stop Job" % (job_name, timeout_ms))
        page.wait_for_timeout(500)
    return True


def _click_start(page, dialog, timeout_ms, attempts=3):
    """Click Start until the dialog closes. An instant click can be ignored
    (the dialog stays open), so press and release the mouse with a short
    pause, and retry. If Start never takes, Cancel the dialog (so it doesn't
    block the IDE) and raise.
    """
    button = dialog.locator(locators.WORKBENCH_JOB_START_BUTTON_SELECTOR)
    per_attempt_ms = max(5000, timeout_ms // attempts)
    for attempt in range(attempts):
        try:
            button.click(delay=150, timeout=per_attempt_ms)
            dialog.wait_for(state="hidden", timeout=per_attempt_ms)
            return
        except Exception:
            if attempt + 1 == attempts:
                try:
                    dialog.locator(locators.WORKBENCH_JOB_CANCEL_BUTTON_SELECTOR).click(timeout=5000)
                except Exception:
                    pass
                raise
            print("[rstudio-local] Start Workbench Job click ignored, retrying (%d/%d)" % (attempt + 2, attempts))
            page.wait_for_timeout(1000)


def start_workbench_job(page, script_path, timeout_ms=30000):
    """In an open session IDE: open `script_path` in the editor (which
    pre-fills the dialog's read-only R Script box), then Workbench Jobs >
    Start Workbench Job > Environment > Start.

    Returns (job_name, elapsed_s), where elapsed_s runs from clicking Start
    to the job appearing in the Workbench Jobs pane.
    """
    run_console_command(page, "file.edit(%s)" % r_string(script_path), timeout_ms=timeout_ms)

    open_workbench_jobs_tab(page, timeout_ms=timeout_ms)
    dialog = open_start_workbench_job_dialog(page, timeout_ms=timeout_ms)

    # Start does nothing unless the Workbench Job Options tab has been shown
    # (it fills in the job's options), so visit it before Environment.
    dialog.get_by_text(locators.WORKBENCH_JOB_OPTIONS_TAB_TEXT, exact=True).first.click()
    page.wait_for_timeout(1500)
    dialog.locator(locators.WORKBENCH_JOB_ENVIRONMENT_TAB_SELECTOR).click()
    # Start is ignored until the Environment tab has loaded.
    dialog.get_by_text(locators.WORKBENCH_JOB_ENVIRONMENT_LOADED_TEXT).first.wait_for(
        state="visible", timeout=timeout_ms
    )
    page.wait_for_timeout(1000)
    _check_script_path(dialog, script_path)

    job_name = posixpath.basename(script_path)
    before_count = _settled_job_count(page, job_name)
    started = time.time()
    _click_start(page, dialog, timeout_ms)
    _wait_for_new_job(page, job_name, before_count, timeout_ms)
    return job_name, time.time() - started


