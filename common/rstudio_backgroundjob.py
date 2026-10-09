"""RStudio Background Jobs: run an R script as a background job of the
session (a separate R process next to the console, listed on the IDE's
Background Jobs tab) and read its state, all through the console.

Unlike a Workbench job these run inside the session, so they end when the
session is quit.
"""
import time

from common import locators
from common.rstudio_console_commands import r_string, read_console_value

# States RStudio reports for a background job that did not finish cleanly.
BACKGROUND_JOB_FAILED_STATES = ("failed", "cancelled")


def _console_visible(page):
    return page.locator(locators.CONSOLE_INPUT_SELECTOR).first.is_visible()


def show_console(page, timeout_ms=10000):
    """Bring the Console tab to the front, if another tab of its pane (e.g.
    Background Jobs) is hiding the console input, and wait for the input."""
    if not _console_visible(page):
        page.get_by_text(locators.CONSOLE_TAB_TEXT, exact=True).first.click()
    page.locator(locators.CONSOLE_INPUT_SELECTOR).first.wait_for(state="visible", timeout=timeout_ms)


def start_background_job(page, script_path, working_dir, job_name=None, timeout_ms=30000, tab_switch_s=5):
    """Start `script_path` as a background job running in `working_dir`,
    named `job_name` (default: the script's file name). Returns the job id.

    Starting a job switches the pane to the Background Jobs tab a moment
    later, hiding the console, so wait up to tab_switch_s for that switch
    and then bring the Console back.
    """
    r_expr = ".rs.api.runScriptJob(path = %s, name = %s, workingDir = %s, importEnv = FALSE)" % (
        r_string(script_path), "NULL" if job_name is None else r_string(job_name), r_string(working_dir),
    )
    job_id = read_console_value(page, r_expr, timeout_ms)
    deadline = time.time() + tab_switch_s
    while time.time() < deadline and _console_visible(page):
        page.wait_for_timeout(250)
    show_console(page)
    if not job_id:
        raise RuntimeError("background job %s did not return a job id" % script_path)
    return job_id


def get_background_job_state(page, job_id, timeout_ms=15000):
    """The state of background job `job_id` as RStudio reports it
    ("running", "succeeded", "failed", "cancelled", ...), "not listed" if
    the session has no such job, or "unknown" if it cannot be read.
    """
    show_console(page)
    r_id = r_string(job_id)
    r_expr = (
        "{s <- tryCatch(.rs.api.getJobState(%s), error = function(e) "
        "tryCatch(.rs.api.listJobs()[[%s]]$state, error = function(e) 'unknown')); "
        "if (is.null(s) || length(s) == 0) 'not listed' else paste(as.character(s), collapse = ' ')}"
        % (r_id, r_id)
    )
    return read_console_value(page, r_expr, timeout_ms)
