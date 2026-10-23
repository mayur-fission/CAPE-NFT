"""Concurrent-session tests: sessions spread over browsers (~5 tabs each),
one R script in all of them, every run timed.

Flow per browser: login -> quit leftovers with its names -> launch each in
      a tab -> setwd home -> empty <output dir>/<session> folder, setwd there
      -> [wait for all browsers] -> source script in all tabs -> wait -> check/delete outputs -> wait for all browsers ->
      close tabs -> close every browser but B1 -> B1 quits all the test's
      sessions in batches of 25 (single_browser_cleanup)
([...] skipped in batch mode: each browser starts as soon as it is ready.)
"""
import os
import threading
import time

from common.perf_report import attach_allure_file

from common.api_ui_combo import launch_sessions_in_tabs, login_and_quit_sessions_named
from common.result_printing import report_failed_runs
from common.parallel_browsers import run_in_parallel_browsers
from config.config import env, env_path
from common.launch_already_created_sessions import (
    SETWD_ATTEMPTS,
    SETWD_RETRY_WAIT_S,
    close_launched_sessions,
    prepare_session_folders,
    run_script_in_launched_sessions,
)
from common.session_actions import set_session_working_directory
from common.single_browser_cleanup import SingleBrowserCleanup
from common.step_log import log_step
from common.session_naming_logic import browser_name_prefixes
from common.parallel_browsers import split_across_browsers
from common.parallel_browsers import MAX_TABS_PER_BROWSER
from common.parallel_browsers import free_port
from common.parallel_browsers import BrowserProgress

# Working directory set in every session right after it launches; the
# script itself then runs in its own <output_dir>/<session name> folder.
SESSION_HOME_DIR = env_path("WORKBENCH_HOME_DIR", "CONCURRENT_SESSIONS_HOME_DIR")
# The stage that lasts as long as the script (BrowserProgress stage_limits)
SCRIPT_STAGE = "running the R script / checking outputs"
# Seconds over which the browsers start their scripts once all are ready,
# evenly (B1 at once, the last one after this long); 0 = all at the same
# moment. Spreading them eases the load peaks - on the machine running the
# browsers and on the server - of every tab starting, and later checking its
# output, at once.
START_SPREAD_S = float(env("CONCURRENT_START_SPREAD_S") or 0)
SPREAD_STAGE = "waiting for its turn to start (CONCURRENT_START_SPREAD_S)"
# Time on top of the script timeout for the output checks (a check that does
# not get through reloads the tab and tries again) before a browser counts as stuck
OUTPUT_CHECK_SLACK_S = 300


def script_names(script_path):
    """The file name of `script_path`, or the names of a {session: path}
    map's scripts, comma-separated."""
    paths = script_path.values() if isinstance(script_path, dict) else [script_path]
    return ", ".join(dict.fromkeys(os.path.basename(p) for p in paths))


def run_script_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                      browser_count, label, script_path, output_dir, output_file_name,
                                      script_timeout_ms, stagger_s=0, attempts=3, launch_timeout_ms=180000,
                                      retry_wait_s=15, delete_output=False, poll_interval_ms=500,
                                      start_together=True, summary_file=None):
    """Run `script_path` in `session_names` spread over `browser_count`
    browsers (started `stagger_s` apart), each run timed. `script_path` can
    also be {session name: script path}, a script of its own per session.

    Flow per browser: login -> quit leftovers -> launch each in a tab -> setwd
          home -> empty `output_dir`/<session> folder, setwd there -> [wait for
          all browsers] -> source script in all tabs -> wait -> check (and
          delete) outputs -> wait
          for all browsers -> close tabs -> close every browser but the first ->
          the first quits all `session_names` in batches of 25

    start_together=False is batch mode: each browser starts as soon as it is
    ready (no [wait]), and one browser's failed launch does not skip the
    others. Cleanup never starts while another browser still runs.
    summary_file: the script's run summary (relative to its session folder),
    whose figures (replicates, ...) go into each run record - see
    run_script_in_launched_sessions().
    Returns (runs, errors): run records, "browser <B>: <error>" per failure.
    """
    log_step("Starting %s: R script %s in %d session(s) over %d browser(s), as user %d",
             label, script_names(script_path), len(session_names), browser_count, user)
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
    # A browser still running its script is not stuck: only after the script
    # timeout (plus time for the output checks) is it captured as one
    progress = BrowserProgress(prefixes, label=label, debug_ports=debug_ports, stage_limits={
        SCRIPT_STAGE: script_timeout_ms / 1000.0 + OUTPUT_CHECK_SLACK_S, SPREAD_STAGE: START_SPREAD_S + 60})
    # Each browser's wait before starting its scripts (CONCURRENT_START_SPREAD_S)
    spread = START_SPREAD_S if start_together and browser_count > 1 else 0
    start_delays = {p: spread * i / (browser_count - 1) if spread else 0 for i, p in enumerate(prefixes)}
    if spread:
        log_step("Starting the scripts over %.0fs (CONCURRENT_START_SPREAD_S): one browser every %.1fs",
                 spread, spread / (browser_count - 1))
    launch_failed = set()
    cleanup = SingleBrowserCleanup(prefixes, user, session_names, launch_args, context_args)
    # Longest a finished browser waits for the others before cleaning up: the
    # slowest legitimate case is the last browser starting late, retrying
    # every launch and running the script to its timeout.
    cleanup_wait_s = int(env("CONCURRENT_CLEANUP_WAIT_S") or (
        script_timeout_ms / 1000.0 + attempts * (launch_timeout_ms / 1000.0 + retry_wait_s)
        + stagger_s * browser_count + spread + 300))

    def _worker(context, prefix):
        return _run_script_in_browser(
            context, prefix, names_by_browser[prefix], user, "%s %s" % (label, prefix.rstrip("_")),
            ready, progress, launch_failed, attempts, launch_timeout_ms, retry_wait_s,
            script_path, output_dir, output_file_name, script_timeout_ms, delete_output, poll_interval_ms,
            start_together, cleanup_wait_s, cleanup, summary_file, start_delays[prefix],
        )

    results = run_in_parallel_browsers(
        browser_name, launch_args, context_args, prefixes, _worker, barriers=(ready, progress),
        stagger_s=stagger_s,
        # a browser frozen inside a Playwright call must not hang the test for ever
        join_timeout_s=stagger_s * browser_count + cleanup_wait_s * 2 + 900,
        debug_ports=debug_ports, on_closed=cleanup.browser_closed,
    )
    cleanup.run_if_missed(browser_name, launch_args, context_args)
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
                           start_together=True, cleanup_wait_s=3600, cleanup=None, summary_file=None,
                           start_delay_s=0):
    """One browser's part of run_script_in_concurrent_sessions(); a browser
    whose launch fails adds its prefix to `launch_failed`. Each stage is
    recorded in `progress` (a BrowserProgress), which also holds the wait
    for the other browsers before cleanup; `cleanup` (a
    SingleBrowserCleanup) quits the sessions from the first browser. Returns (runs, error): its run
    records and the error that failed it, else None."""
    where = " in browser %s" % prefix.rstrip("_")
    home_url = before_ids = prepared = None
    launched, runs, error = [], [], None
    try:
        try:
            progress.set(prefix, "logging in")
            home_url, before_ids = login_and_quit_sessions_named([context], user, session_names)
            if cleanup is not None:
                cleanup.note_login(prefix, home_url, before_ids)
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
                            # Not fatal for the browser: next each session sets its own
                            # folder (with retries), and only a session that fails there
                            # fails - not this browser's other sessions.
                            log_step("WARNING: could not set the working directory to %s in session %s "
                                     "(carrying on, the next step sets it again): %s",
                                     SESSION_HOME_DIR, session.session_name, exc)
                            break
                        log_step("Could not set the working directory in session %s (attempt %d of %d), "
                                 "retrying in %ds: %s", session.session_name, attempt, SETWD_ATTEMPTS,
                                 SETWD_RETRY_WAIT_S, exc)
                        session.page.wait_for_timeout(SETWD_RETRY_WAIT_S * 1000)
            # Each session's own empty <output_dir>/<session> folder, set as its
            # working directory now - before waiting for the other browsers - so
            # that afterwards the scripts are only submitted and start within
            # seconds of each other in every browser.
            progress.set(prefix, "preparing the session folders")
            prepared = prepare_session_folders(launched, output_dir, per_session_dir=True)
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
                     script_names(script_path))
            if start_delay_s:
                progress.set(prefix, SPREAD_STAGE)
                log_step("Starting this browser's scripts in %.1fs (CONCURRENT_START_SPREAD_S)", start_delay_s)
                time.sleep(start_delay_s)
        else:
            log_step("All %d session(s) of this browser are ready - starting the R script %s in them now",
                     len(launched), script_names(script_path))
        progress.set(prefix, SCRIPT_STAGE)
        started = time.time()
        runs += run_script_in_launched_sessions(
            launched, script_path, working_dir=output_dir, timeout_ms=script_timeout_ms,
            output_path=output_file_name, per_session_dir=True, delete_output=delete_output,
            poll_interval_ms=poll_interval_ms, prepared=prepared, summary_file=summary_file,
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
        if cleanup is not None:
            cleanup.run(context, prefix)
    return runs, error
