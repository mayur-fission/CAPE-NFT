"""Concurrent-session tests: sessions spread over browsers (~5 tabs each),
one R script in all of them, every run timed.

Flow per browser: login -> quit leftovers with its names -> launch each in
      a tab -> setwd home -> [wait for all browsers] -> source script in all
      tabs -> wait -> check/delete outputs -> wait for all browsers ->
      close tabs -> quit sessions
([...] skipped in batch mode: each browser starts as soon as it is ready.)
"""
import os
import threading
import time

from common.perf_report import attach_allure_file

from common.api_ui_combo import launch_sessions_in_tabs, login_and_quit_sessions_named, quit_ui_sessions_named
from common.result_printing import report_failed_runs
from common.parallel_browsers import run_in_parallel_browsers
from config.config import env, env_path
from common.launch_already_created_sessions import (
    SETWD_ATTEMPTS,
    SETWD_RETRY_WAIT_S,
    close_launched_sessions,
    run_script_in_launched_sessions,
)
from common.session_actions import login_to_posit_workbench, set_session_working_directory
from common.step_log import log_step
from common.session_naming_logic import browser_name_prefixes
from common.parallel_browsers import split_across_browsers
from common.parallel_browsers import MAX_TABS_PER_BROWSER
from common.parallel_browsers import free_port
from common.parallel_browsers import BrowserProgress

# Working directory set in every session right after it launches; the
# script itself then runs in its own <output_dir>/<session name> folder.
SESSION_HOME_DIR = env_path("WORKBENCH_HOME_DIR", "CONCURRENT_SESSIONS_HOME_DIR")


def run_script_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                      browser_count, label, script_path, output_dir, output_file_name,
                                      script_timeout_ms, stagger_s=0, attempts=3, launch_timeout_ms=180000,
                                      retry_wait_s=15, delete_output=False, poll_interval_ms=500,
                                      start_together=True):
    """Run `script_path` in `session_names` spread over `browser_count`
    browsers (started `stagger_s` apart), each run timed.

    Flow per browser: login -> quit leftovers -> launch each in a tab -> setwd
          home -> [wait for all browsers] -> source script in all tabs (each in
          `output_dir`/<session>) -> wait -> check (and delete) outputs -> wait
          for all browsers -> close tabs -> quit sessions

    start_together=False is batch mode: each browser starts as soon as it is
    ready (no [wait]), and one browser's failed launch does not skip the
    others. Cleanup never starts while another browser still runs.
    Returns (runs, errors): run records, "browser <B>: <error>" per failure.
    """
    log_step("Starting %s: R script %s in %d session(s) over %d browser(s), as user %d",
             label, os.path.basename(script_path), len(session_names), browser_count, user)
    prefixes = browser_name_prefixes(browser_count)
    names_by_browser = dict(zip(prefixes, split_across_browsers(session_names, browser_count)))
    for prefix, names in names_by_browser.items():
        print("\n[rstudio-local] browser %s: %d session(s) - %s" % (prefix.rstrip("_"), len(names), ", ".join(names)))
        if len(names) > MAX_TABS_PER_BROWSER:
            print("\n[rstudio-local] warning: browser %s has %d tabs, over %d - tabs may stall; use more browsers"
                  % (prefix.rstrip("_"), len(names), MAX_TABS_PER_BROWSER))

    ready = threading.Barrier(browser_count)
    # Each Chromium gets a DevTools port, so a stuck one can be captured from another thread
    debug_ports = {p: free_port() for p in prefixes} if browser_name == "chromium" else {}
    progress = BrowserProgress(prefixes, label=label, debug_ports=debug_ports)
    launch_failed = set()
    # Longest a finished browser waits for the others before cleaning up: the
    # slowest legitimate case is the last browser starting late, retrying
    # every launch and running the script to its timeout.
    cleanup_wait_s = int(env("CONCURRENT_CLEANUP_WAIT_S") or (
        script_timeout_ms / 1000.0 + attempts * (launch_timeout_ms / 1000.0 + retry_wait_s)
        + stagger_s * browser_count + 300))

    def _worker(context, prefix):
        return _run_script_in_browser(
            context, prefix, names_by_browser[prefix], user, "%s %s" % (label, prefix.rstrip("_")),
            ready, progress, launch_failed, attempts, launch_timeout_ms, retry_wait_s,
            script_path, output_dir, output_file_name, script_timeout_ms, delete_output, poll_interval_ms,
            start_together, cleanup_wait_s, launch_args, context_args,
        )

    results = run_in_parallel_browsers(
        browser_name, launch_args, context_args, prefixes, _worker, barriers=(ready, progress),
        stagger_s=stagger_s,
        # a browser frozen inside a Playwright call must not hang the test for ever
        join_timeout_s=stagger_s * browser_count + cleanup_wait_s * 2 + 900,
        debug_ports=debug_ports,
    )
    runs, errors = [], []
    for prefix, result, error in results:
        if result is not None:
            browser_runs, error = result
            runs += browser_runs
        else:
            # a browser that never returned (stuck) or died: capture it if that
            # has not happened yet, and keep its sessions in the results as failed
            if isinstance(error, TimeoutError):
                progress.capture([prefix])
            reason = ("browser stuck: %s" % progress.stage(prefix) if isinstance(error, TimeoutError)
                      else "browser failed: %s" % " ".join(str(error).split())[:150])
            seen = {r["session_name"] for r in runs}
            runs += [{"session_name": name, "started": None, "ended": None, "status": reason}
                     for name in names_by_browser[prefix] if name not in seen]
        if error is not None:
            errors.append("browser %s: %s" % (prefix.rstrip("_"), error))
    # attach the stuck-browser captures from the test's own thread (Allure is per thread)
    for path in progress.captured:
        attach_allure_file(path)
    return runs, errors


def _run_script_in_browser(context, prefix, session_names, user, label, ready, progress, launch_failed,
                           attempts, launch_timeout_ms, retry_wait_s, script_path, output_dir,
                           output_file_name, script_timeout_ms, delete_output=False, poll_interval_ms=500,
                           start_together=True, cleanup_wait_s=3600, launch_args=None, context_args=None):
    """One browser's part of run_script_in_concurrent_sessions(); a browser
    whose launch fails adds its prefix to `launch_failed`. Each stage is
    recorded in `progress` (a BrowserProgress), which also holds the wait
    for the other browsers before cleanup. Returns (runs, error): its run
    records and the error that failed it, else None."""
    where = " in browser %s" % prefix.rstrip("_")
    home_url = before_ids = None
    launched, runs, error = [], [], None
    try:
        try:
            progress.set(prefix, "logging in")
            home_url, before_ids = login_and_quit_sessions_named([context], user, session_names)
            progress.set(prefix, "launching sessions")
            launched, failed = launch_sessions_in_tabs(
                context, home_url, session_names, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
                retry_wait_s=retry_wait_s, where=where,
            )
            assert not failed, "sessions that failed to launch: %s" % failed
            progress.set(prefix, "setting working directories")
            for session in launched:
                for attempt in range(1, SETWD_ATTEMPTS + 1):
                    try:
                        set_session_working_directory(session.page, path=SESSION_HOME_DIR)
                        log_step("Working directory set to %s in session %s", SESSION_HOME_DIR, session.session_name)
                        break
                    except Exception as exc:
                        if attempt == SETWD_ATTEMPTS:
                            # Not fatal for the browser: right before the script each session
                            # sets its own folder again (with retries), and only a session
                            # that fails there fails - not this browser's other sessions.
                            log_step("WARNING: could not set the working directory to %s in session %s "
                                     "(carrying on, the script step sets it again): %s",
                                     SESSION_HOME_DIR, session.session_name, exc)
                            break
                        log_step("Could not set the working directory in session %s (attempt %d of %d), "
                                 "retrying in %ds: %s", session.session_name, attempt, SETWD_ATTEMPTS,
                                 SETWD_RETRY_WAIT_S, exc)
                        session.page.wait_for_timeout(SETWD_RETRY_WAIT_S * 1000)
        except Exception:
            launch_failed.add(prefix)
            raise
        finally:
            if start_together:
                progress.set(prefix, "waiting for the other browsers to be ready")
                log_step("%d of %d session(s) ready in this browser - waiting for the other browsers",
                         len(launched), len(session_names))
                ready.wait()
        if start_together:
            if launch_failed:
                raise AssertionError("scripts skipped: launch failed in browser(s) %s"
                                     % sorted(p.rstrip("_") for p in launch_failed))
            log_step("All browsers are ready - starting the R script %s in every session",
                     os.path.basename(script_path))
        else:
            log_step("All %d session(s) of this browser are ready - starting the R script %s in them now",
                     len(launched), os.path.basename(script_path))
        progress.set(prefix, "running the R script / checking outputs")
        started = time.time()
        runs += run_script_in_launched_sessions(
            launched, script_path, working_dir=output_dir, timeout_ms=script_timeout_ms,
            output_path=output_file_name, per_session_dir=True, delete_output=delete_output,
            poll_interval_ms=poll_interval_ms,
        )
        failures = report_failed_runs(runs, label, started)
        assert not failures, "runs that did not finish: %s" % failures
    except Exception as exc:
        error = exc
    finally:
        log_step("This browser is done - waiting for the other browsers to finish before cleanup")
        if not progress.done_and_wait(prefix, cleanup_wait_s) and error is None:
            error = AssertionError("cleaned up without waiting for browser(s) still not done after %ds: %s"
                                   % (cleanup_wait_s, progress.pending()))
        close_launched_sessions(launched)
        if launched:
            log_step("Closed %d session tab(s)", len(launched))
        if home_url is not None:
            _quit_browser_sessions(context, home_url, before_ids, session_names, user, prefix,
                                   launch_args, context_args)
    return runs, error


def _quit_browser_sessions(context, home_url, before_ids, session_names, user, prefix, launch_args, context_args):
    """quit_ui_sessions_named() for one browser's sessions. If that browser
    has died meanwhile (e.g. Chromium crashed or ran out of memory), a fresh
    one is launched and logged in to quit them, so they are not left running
    on the server. Errors are printed, not raised."""
    what = "browser %s" % prefix.rstrip("_")
    browser = context.browser
    if browser is None or browser.is_connected():
        quit_ui_sessions_named(context, home_url, before_ids, session_names, what)
        return
    log_step("Browser %s is no longer running (crashed or closed) - opening a new browser to quit its sessions",
             prefix.rstrip("_"))
    try:
        fresh = browser.browser_type.launch(**(launch_args or {}))
        try:
            fresh_context = fresh.new_context(**{**(context_args or {}), "ignore_https_errors": True})
            login_page = fresh_context.new_page()
            login_to_posit_workbench(login_page, user=user)
            login_page.close()
            quit_ui_sessions_named(fresh_context, home_url, before_ids, session_names, what)
        finally:
            fresh.close()
    except Exception as exc:
        log_step("Could not quit the RStudio sessions created by this test from a new browser either "
                 "(they may need deleting by hand: %s): %s", ", ".join(session_names), exc)


