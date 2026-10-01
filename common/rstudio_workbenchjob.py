"""Workbench Jobs automation: launch a session, open the Workbench Jobs pane,
and start an R script as a Workbench job via the Start Workbench Job dialog.
Also multi-session flows that start a job (or source a script in the
console) in each session's own tab and close the tab straight away.
"""
import posixpath
import time
from collections import namedtuple

from common import locators
from common.rstudio_console_commands import (
    get_file_size,
    is_console_command_done,
    r_string,
    run_console_command,
    submit_console_command,
)
from common.rstudio_session_helper import launch_new_session, open_existing_session
from common.session_actions import create_folder, delete_file, set_session_working_directory

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


def launch_sessions_and_start_jobs(context, home_url, session_count, script_path=DEFAULT_WORKBENCH_JOB_SCRIPT):
    """Launch `session_count` new sessions, each in its own tab, start
    `script_path` as a Workbench job in each and close the tab as soon as the
    job has started. Returns the names of the sessions launched, including
    one whose job failed to start.
    """
    session_names = []
    for _ in range(session_count):
        tab = context.new_page()
        try:
            launch = launch_new_session(tab, home_url)
            session_names.append(launch.session_name)
            job_name, submit_elapsed = start_workbench_job(tab, script_path)
            print(
                "\n[rstudio-local] session %s launched in %.2fs, job %s started in %.2fs (status: %s)"
                % (launch.session_name, launch.elapsed_s, job_name, submit_elapsed,
                   get_job_status(tab, job_name))
            )
        finally:
            tab.close()
    return session_names


def reopen_sessions_and_stop_jobs(page, home_url, session_names, job_name):
    """Reopen each session in `page` and stop a running `job_name` job.
    Returns a list of failure messages (empty if every job was stopped).
    """
    failures = []
    for session_name in session_names:
        try:
            reopen = open_existing_session(page, home_url, session_name)
            stopped = stop_workbench_job(page, job_name)
            print(
                "\n[rstudio-local] session %s reopened in %.2fs, %s job %s (status: %s)"
                % (session_name, reopen.elapsed_s, job_name,
                   "stopped" if stopped else "was not running", get_job_status(page, job_name))
            )
            if not stopped:
                failures.append("%s: no running %s job to stop" % (session_name, job_name))
        except Exception as exc:
            failures.append("%s: %s" % (session_name, exc))
    return failures


def launch_sessions_and_source_script(context, home_url, session_count, script_path=DEFAULT_WORKBENCH_JOB_SCRIPT,
                                      working_dir=None):
    """Launch `session_count` new sessions, each in its own tab, setwd to
    `working_dir` (default RSTUDIO_WORKING_DIR; skipped if neither is set),
    source `script_path` in the console and close the tab without waiting for
    the script to finish.
    """
    source_command = "source(%s)" % r_string(script_path)
    for _ in range(session_count):
        tab = context.new_page()
        try:
            launch = launch_new_session(tab, home_url)
            set_session_working_directory(tab, path=working_dir)
            submit_console_command(tab, source_command)
            # The console echoes the command once R has accepted it.
            tab.locator(locators.CONSOLE_OUTPUT_SELECTOR).get_by_text(source_command).first.wait_for(
                state="visible", timeout=10000
            )
            print(
                "\n[rstudio-local] session %s launched in %.2fs, %s submitted in the console"
                % (launch.session_name, launch.elapsed_s, source_command)
            )
        finally:
            tab.close()


def _check_output_file(page, record, output_path, delete=False, remove_dir=None):
    """Set record["status"] to an error unless `output_path` exists. With
    `delete`, then delete it, and remove the folder `remove_dir` (when given)
    if that leaves it empty.
    """
    try:
        size = get_file_size(page, output_path)
    except Exception as exc:
        record["status"] = "could not check %s: %s" % (output_path, exc)
        return
    if size is None:
        record["status"] = "%s was not created" % output_path
    else:
        print("\n[rstudio-local] session %s: %s is %d bytes" % (record["session_name"], output_path, size))
    if not delete or size is None:
        return
    try:
        if not delete_file(page, output_path):
            record["status"] = "could not delete %s" % output_path
            return
        if remove_dir:
            run_console_command(
                page,
                "local({d <- %s; if (length(list.files(d, all.files=TRUE, no..=TRUE)) == 0) "
                "unlink(d, recursive=TRUE)})" % r_string(remove_dir),
                timeout_ms=10000,
            )
        print("\n[rstudio-local] session %s: deleted %s" % (record["session_name"], output_path))
    except Exception as exc:
        record["status"] = "could not delete %s: %s" % (output_path, exc)


def launch_sessions_and_run_script(context, home_url, session_count, script_path=DEFAULT_WORKBENCH_JOB_SCRIPT,
                                   working_dir=None, timeout_ms=1800000, poll_interval_ms=500, output_path=None,
                                   per_session_dir=False, delete_output=False):
    """Launch `session_count` new sessions, each in its own tab, and setwd to
    `working_dir` (default RSTUDIO_WORKING_DIR; skipped if neither is set).
    With per_session_dir, each session instead gets its own
    `working_dir`/<session name> folder (created if missing), so sessions
    writing the same relative file don't overwrite each other.
    Once every session is up, source `script_path` in each console and poll
    the tabs in turn until every run has finished or timed out, so the
    scripts run concurrently. With `output_path` (relative paths resolve
    against the session's working directory), a run only counts as "ok" if
    that file exists once the script has finished; with delete_output it is
    then deleted (and the per-session folder too, if left empty). Tabs are
    closed before returning.

    Returns one record per session in the common.script_timings format
    ("session_name", "started", "ended", "status"), status "ok", "timed out"
    or the error.
    """
    if per_session_dir and not working_dir:
        raise ValueError("per_session_dir needs a working_dir")
    source_command = "source(%s)" % r_string(script_path)
    tabs, runs, session_dirs, pending = [], [], [], []
    try:
        for i in range(session_count):
            tab = context.new_page()
            tabs.append(tab)
            record = {"session_name": "session %d" % (i + 1), "started": None, "ended": None, "status": "not run"}
            runs.append(record)
            session_dirs.append(None)
            try:
                launch = launch_new_session(tab, home_url)
                record["session_name"] = launch.session_name
                if per_session_dir:
                    session_dirs[i] = posixpath.join(working_dir, launch.session_name)
                    create_folder(tab, session_dirs[i])
                set_session_working_directory(tab, path=session_dirs[i] or working_dir)
                print("\n[rstudio-local] session %s launched in %.2fs" % (launch.session_name, launch.elapsed_s))
            except Exception as exc:
                record["status"] = "launch failed: %s" % exc

        for tab, record, session_dir in zip(tabs, runs, session_dirs):
            if record["status"] != "not run":
                continue
            try:
                marker, record["started"] = submit_console_command(tab, source_command)
                pending.append((tab, marker, record, session_dir))
            except Exception as exc:
                record["status"] = "submit failed: %s" % exc

        while pending:
            for item in list(pending):
                tab, marker, record, session_dir = item
                try:
                    done = is_console_command_done(tab, marker)
                except Exception as exc:
                    record["status"] = str(exc)
                    pending.remove(item)
                    continue
                if done:
                    record["ended"], record["status"] = time.time(), "ok"
                    pending.remove(item)
                    print(
                        "\n[rstudio-local] session %s: %s took %.2fs"
                        % (record["session_name"], source_command, record["ended"] - record["started"])
                    )
                    if output_path:
                        _check_output_file(tab, record, output_path, delete=delete_output, remove_dir=session_dir)
                elif time.time() - record["started"] >= timeout_ms / 1000.0:
                    record["status"] = "timed out"
                    pending.remove(item)
            if pending:
                pending[0][0].wait_for_timeout(poll_interval_ms)
    finally:
        for tab in tabs:
            tab.close()
    return runs
