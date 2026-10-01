"""Workbench Jobs automation: launch a session, open the Workbench Jobs pane,
and start an R script as a Workbench job via the Start Workbench Job dialog.
"""
import posixpath
import time
from collections import namedtuple

from common import locators
from common.rstudio_console_commands import r_string, run_console_command
from common.rstudio_session_helper import launch_new_session

DEFAULT_WORKBENCH_JOB_SCRIPT = "/fsx/data/sample_run_sleep.R"

WorkbenchJobRun = namedtuple(
    "WorkbenchJobRun", ["session_launch", "job_name", "submit_elapsed_s", "elapsed_s", "status"]
)


def open_workbench_jobs_tab(page, timeout_ms=10000):
    """Click the Workbench Jobs tab next to the Console."""
    tab = page.locator(locators.WORKBENCH_JOBS_TAB_SELECTOR)
    tab.wait_for(state="visible", timeout=timeout_ms)
    tab.click()


def open_start_workbench_job_dialog(page, timeout_ms=10000, attempts=3):
    """Click Start Workbench Job in the Workbench Jobs pane and return the
    dialog locator. Retries the click: right after the pane opens, the button
    is visible but a click can be ignored.
    """
    button = page.locator(locators.START_WORKBENCH_JOB_BUTTON_SELECTOR)
    dialog = page.locator("[role='dialog']").filter(has_text=locators.WORKBENCH_JOB_DIALOG_TITLE).last
    button.wait_for(state="visible", timeout=timeout_ms)
    for attempt in range(attempts):
        button.click()
        try:
            dialog.wait_for(state="visible", timeout=timeout_ms)
            return dialog
        except Exception:
            if attempt + 1 == attempts:
                raise


def _check_script_path(dialog, script_path):
    """The R Script box is read-only and pre-filled from the active editor
    tab; check that it holds `script_path`.
    """
    value = dialog.locator(locators.WORKBENCH_JOB_SCRIPT_INPUT_SELECTOR).input_value()
    if value != script_path:
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


def wait_for_job_status(page, job_name, timeout_ms=600000, poll_interval_ms=2000):
    """Poll until the newest `job_name` entry shows a final state, and return
    that state. Raises RuntimeError on timeout.
    """
    open_workbench_jobs_tab(page)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        status = get_job_status(page, job_name) or ""
        for state in locators.WORKBENCH_JOB_FINAL_STATES:
            if state in status:
                return state
        page.wait_for_timeout(poll_interval_ms)

    raise RuntimeError("Workbench job %r did not finish within %dms" % (job_name, timeout_ms))


def start_workbench_job(page, script_path=DEFAULT_WORKBENCH_JOB_SCRIPT, timeout_ms=30000):
    """In an open session IDE: open `script_path` in the editor (which
    pre-fills the dialog's read-only R Script box), then Workbench Jobs >
    Start Workbench Job > Environment > Start.

    Returns (job_name, elapsed_s), where elapsed_s runs from clicking Start
    to the job appearing in the Workbench Jobs pane.
    """
    run_console_command(page, "file.edit(%s)" % r_string(script_path), timeout_ms=timeout_ms)

    open_workbench_jobs_tab(page, timeout_ms=timeout_ms)
    dialog = open_start_workbench_job_dialog(page, timeout_ms=timeout_ms)

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
    dialog.locator(locators.WORKBENCH_JOB_START_BUTTON_SELECTOR).click()
    dialog.wait_for(state="hidden", timeout=timeout_ms)
    _wait_for_new_job(page, job_name, before_count, timeout_ms)
    return job_name, time.time() - started


def run_workbench_job(page, home_url, script_path=DEFAULT_WORKBENCH_JOB_SCRIPT, session_name=None,
                      wait_for_completion=True, timeout_ms=600000):
    """Launch a new session, then start `script_path` as a Workbench job.

    With wait_for_completion, waits for the job to reach a final state and
    raises AssertionError if it failed.

    Returns WorkbenchJobRun(session_launch, job_name, submit_elapsed_s,
    elapsed_s, status): submit_elapsed_s runs from clicking Start to the job
    appearing, elapsed_s from clicking Start to its final state (None and
    status None when not waiting).
    """
    session_launch = launch_new_session(page, home_url, session_name=session_name)

    started = time.time()
    job_name, submit_elapsed = start_workbench_job(page, script_path)

    if not wait_for_completion:
        return WorkbenchJobRun(session_launch, job_name, submit_elapsed, None, None)

    status = wait_for_job_status(page, job_name, timeout_ms=timeout_ms)
    elapsed = time.time() - started
    assert status not in locators.WORKBENCH_JOB_FAILED_STATES, (
        "Workbench job %r ended as %s" % (job_name, status)
    )
    return WorkbenchJobRun(session_launch, job_name, submit_elapsed, elapsed, status)
