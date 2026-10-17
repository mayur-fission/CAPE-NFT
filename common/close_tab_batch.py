"""Helpers for tests/test_run_r_close_browser_batch_execution.py: the
close-tab test in batches, like test_concurrent_sessions_batch_execution.

Sessions are spread over several browsers (a handful of tabs each, see
common.concurrent_sessions). Each browser, as soon as its own sessions are
launched, sources the R script in all of them, closes their tabs and
watches them for TEST_DURATION seconds (common.session_monitor: a session
that leaves the list or stops being Active/Executing is a drop). It does
not wait for the other browsers to start. Stopping the scripts and quitting
the sessions happen only once every browser has finished watching, so no
cleanup lightens the load while another browser is still being measured.
"""
import os

from config.perf_report import attach_allure_file, write_json_report

from common.api_ui_combo import (
    browser_name_prefixes,
    launch_sessions_in_tabs,
    login_and_quit_sessions_named,
    quit_ui_sessions_named,
    run_in_parallel_browsers,
)
from common.concurrent_sessions import _BrowserProgress, _free_port, split_across_browsers
from common.config import EVIDENCE_DIR, env
from common.launch_already_created_sessions import SETWD_ATTEMPTS, SETWD_RETRY_WAIT_S, close_launched_sessions
from common.rstudio_console_commands import r_source_command, submit_console_command
from common.session_actions import set_session_working_directory
from common.session_monitor import monitor_sessions, stop_console_scripts
from common.step_log import log_step


def run_close_tab_batch(browser_name, launch_args, context_args, user, session_names, browser_count, label,
                        script_path, duration_s, monitor_interval_s, stop_before_cleanup=True, stagger_s=0,
                        attempts=3, launch_timeout_ms=180000, retry_wait_s=15):
    """Run the batch close-tab scenario over `browser_count` browsers.

    Returns (drops, stop_failures, errors): every dropped session (see
    monitor_sessions(), plus "browser"), {session: error} for scripts that
    could not be stopped, and "browser <B>: <error>" per failed browser
    (including sessions that failed to launch). Also writes
    evidence/session_drops_<label>.json when there are drops; each browser
    writes its own evidence/session_monitor_<label>_<B>.csv.
    """
    prefixes = browser_name_prefixes(browser_count)
    names_by_browser = dict(zip(prefixes, split_across_browsers(session_names, browser_count)))
    log_step("Starting %s: R script %s in %d session(s) over %d browser(s), tabs closed for %.0fs, as user %d",
             label, os.path.basename(script_path), len(session_names), browser_count, duration_s, user)
    for prefix, names in names_by_browser.items():
        print("\n[rstudio-local] browser %s: %d session(s) - %s" % (prefix.rstrip("_"), len(names), ", ".join(names)))

    debug_ports = {p: _free_port() for p in prefixes} if browser_name == "chromium" else {}
    progress = _BrowserProgress(prefixes, label=label, debug_ports=debug_ports)
    # Longest a finished browser waits for the others: the last one starting
    # late, retrying every launch and then watching for the full duration.
    cleanup_wait_s = int(env("CONCURRENT_CLEANUP_WAIT_S") or (
        duration_s + attempts * (launch_timeout_ms / 1000.0 + retry_wait_s) + stagger_s * browser_count + 600))

    def _worker(context, prefix):
        return _close_tab_batch_in_browser(
            context, prefix, names_by_browser[prefix], user, label, script_path, duration_s,
            monitor_interval_s, stop_before_cleanup, attempts, launch_timeout_ms, retry_wait_s,
            progress, cleanup_wait_s,
        )

    results = run_in_parallel_browsers(
        browser_name, launch_args, context_args, prefixes, _worker, barriers=(progress,), stagger_s=stagger_s,
        join_timeout_s=stagger_s * browser_count + cleanup_wait_s * 2 + 900, debug_ports=debug_ports,
    )
    drops, stop_failures, errors = [], {}, []
    for prefix, result, error in results:
        browser_id = prefix.rstrip("_")
        if result is not None:
            browser_drops, browser_stop_failures, failed_launches = result[0]
            error = result[1]
            drops += [dict(d, browser=browser_id) for d in browser_drops]
            stop_failures.update(browser_stop_failures)
            if failed_launches:
                errors.append("browser %s: sessions that failed to launch: %s" % (browser_id, failed_launches))
        elif isinstance(error, TimeoutError):
            progress.capture([prefix])
        if error is not None:
            errors.append("browser %s: %s" % (browser_id, error))
    if drops:
        json_path = os.path.join(EVIDENCE_DIR, "session_drops_%s.json" % label)
        write_json_report(json_path, drops)
        attach_allure_file(json_path)
    for path in progress.captured:
        attach_allure_file(path)
    log_step("%s: %d session(s), %d dropped, %d script(s) not stopped, %d browser error(s)",
             label, len(session_names), len(drops), len(stop_failures), len(errors))
    return drops, stop_failures, errors


def _close_tab_batch_in_browser(context, prefix, session_names, user, label, script_path, duration_s,
                                monitor_interval_s, stop_before_cleanup, attempts, launch_timeout_ms,
                                retry_wait_s, progress, cleanup_wait_s):
    """One browser's part of run_close_tab_batch(). Returns ((drops,
    stop_failures, failed_launches), error)."""
    browser_id = prefix.rstrip("_")
    home_url = before_ids = None
    launched, failed, drops, stop_failures, error = [], [], [], {}, None
    launched_pages_closed = False
    source_command = r_source_command(script_path)
    try:
        progress.set(prefix, "logging in")
        home_url, before_ids = login_and_quit_sessions_named([context], user, session_names)
        progress.set(prefix, "launching sessions")
        launched, failed = launch_sessions_in_tabs(
            context, home_url, session_names, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
            retry_wait_s=retry_wait_s, where=" in browser %s" % browser_id,
        )
        progress.set(prefix, "starting the R script")
        started = []
        for session in launched:
            try:
                for attempt in range(1, SETWD_ATTEMPTS + 1):
                    try:
                        set_session_working_directory(session.page)
                        break
                    except Exception:
                        if attempt == SETWD_ATTEMPTS:
                            raise
                        session.page.wait_for_timeout(SETWD_RETRY_WAIT_S * 1000)
                submit_console_command(session.page, source_command)   # returns once R took the command
                started.append(session.session_name)
                log_step("%s submitted in session %s", source_command, session.session_name)
            except Exception as exc:
                failed.append("%s: could not start the script: %s" % (session.session_name, exc))
                log_step("Could not start the script in session %s: %s", session.session_name, exc)
        close_launched_sessions(launched)
        log_step("Closed the tabs of %d session(s) - their scripts keep running on the server", len(launched))
        launched_pages_closed = True
        progress.set(prefix, "watching the sessions with their tabs closed")
        home_page = context.new_page()
        try:
            drops = monitor_sessions(home_page, home_url, started, duration_s, monitor_interval_s,
                                     "%s_%s" % (label, browser_id))
        finally:
            home_page.close()
    except Exception as exc:
        error = exc
    finally:
        log_step("This browser is done - waiting for the other browsers before stopping the scripts and cleanup")
        if not progress.done_and_wait(prefix, cleanup_wait_s) and error is None:
            error = AssertionError("cleaned up without waiting for browser(s) still not done after %ds: %s"
                                   % (cleanup_wait_s, progress.pending()))
        if not launched_pages_closed:
            close_launched_sessions(launched)
        if home_url is not None:
            if stop_before_cleanup:
                dropped = {d["session"] for d in drops}
                alive = [s.session_name for s in launched if s.session_name not in dropped]
                stop_failures = stop_console_scripts(context, home_url, alive)
            quit_ui_sessions_named(context, home_url, before_ids, session_names, "browser %s" % browser_id)
    return (drops, stop_failures, failed), error
