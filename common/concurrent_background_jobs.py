"""An R script run as an RStudio background job in many sessions at once:
sessions spread over browsers (5 tabs each), all logged in as one user
(common/concurrent_file_crud.py's flow, in batches). Each browser starts
the job in all its sessions as soon as its own sessions are launched -
it does not wait for the other browsers - checks the jobs every
`check_interval_s`, and once the duration is up stops each job. The
sessions are quit once every browser is done.

One record per session: "started" = job start, "ended" = job stopped.
"""
import posixpath
import time

from common.api_ui_combo import start_background_jobs
from common.concurrent_file_crud import run_loops_in_concurrent_sessions
from common.rstudio_backgroundjob import (
    BACKGROUND_JOB_ACTIVE_STATES,
    BACKGROUND_JOB_FAILED_STATES,
    get_background_job_state,
    stop_background_job,
)
from common.rstudio_console_commands import wait_for_console_ready
from common.step_log import log_step
from config.config import env

# Time allowed per session to check and stop its job, on top of the duration.
STOP_GRACE_S = float(env("BACKGROUND_JOB_STOP_GRACE_S", "120"))


def run_background_jobs_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                               browser_count, label, script_path, output_dir, duration_s,
                                               check_interval_s=60, stagger_s=0, attempts=3,
                                               launch_timeout_ms=180000, retry_wait_s=15):
    """Run `script_path` as a background job in every one of
    `session_names` (working in `output_dir`/<session name>, emptied
    first), spread over `browser_count` browsers started `stagger_s`
    apart, for `duration_s` from each browser's start, then stop it.

    Returns (records, errors): a record per session and "browser <B>:
    <error>" per browser with a failed job or launch.
    """
    script = "%s (background job)" % posixpath.basename(script_path)

    def _prepare(session):
        wait_for_console_ready(session.page)

    def _run_jobs(launched, records):
        jobs, started = start_background_jobs(launched, script_path, output_dir)
        for record in jobs:
            record["script"] = script
        records += jobs
        log_step("Started %d of %d background job(s) - running them for %.0fs",
                 len(started), len(launched), duration_s)
        deadline = time.time() + duration_s
        watching = list(started)
        while watching and time.time() < deadline:
            time.sleep(max(0.0, min(check_interval_s, deadline - time.time())))
            watching = [job for job in watching if _job_still_ok(*job)]
        for job in watching:
            _stop_job(*job)
        return ["%s: %s" % (r["session_name"], r["status"]) for r in jobs if r["status"] != "ok"]

    return run_loops_in_concurrent_sessions(
        browser_name, launch_args, context_args, user, session_names, browser_count, label,
        "background job %s (for %.0fs, then stopped)" % (posixpath.basename(script_path), duration_s),
        _prepare, _run_jobs, duration_s + STOP_GRACE_S * max(1, len(session_names) // browser_count + 1),
        stagger_s=stagger_s, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
        retry_wait_s=retry_wait_s, start_together=False,
    )


def _job_state(page, job_id):
    try:
        return get_background_job_state(page, job_id)
    except Exception as exc:
        return "unknown (%s)" % exc


def _job_still_ok(page, job_id, record):
    """Check one job; a failed, cancelled, vanished or finished job ends its
    record (failed unless it succeeded). Returns True while it runs."""
    state = _job_state(page, job_id)
    if state in BACKGROUND_JOB_ACTIVE_STATES:
        return True
    record["ended"] = time.time()
    if state in BACKGROUND_JOB_FAILED_STATES or state == "not listed" or state.startswith("unknown"):
        record["status"] = "background job %s after %.0fs" % (state, record["ended"] - record["started"])
    else:
        record["status"] = "ok"
    log_step("Session %s: background job %s is %s after %.0fs - no longer watched",
             record["session_name"], job_id, state, record["ended"] - record["started"])
    return False


def _stop_job(page, job_id, record):
    """Stop a job still running at the end of the duration; its record is
    ok once it has stopped."""
    try:
        state = stop_background_job(page, job_id)
    except Exception as exc:
        record["status"] = "background job not stopped: %s" % exc
        log_step("Session %s: could not stop background job %s: %s", record["session_name"], job_id, exc)
        return
    record["ended"] = time.time()
    record["status"] = "ok"
    log_step("Session %s: background job %s stopped (%s) after %.0fs",
             record["session_name"], job_id, state, record["ended"] - record["started"])
