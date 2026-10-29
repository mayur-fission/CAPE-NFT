"""Text file create / open / edit / delete loops (common/text_file_crud.py)
in many sessions at once: sessions spread over browsers, all logged in as
one user, every loop's steps timed.

Flow per browser: login -> quit leftovers with its names -> launch each in
      a tab -> setwd, Files pane, auto_test folder -> [wait for all
      browsers] -> loops until the duration is up -> wait for all browsers
      -> close tabs -> close every browser but B1 -> B1 quits all the test's
      sessions (single_browser_cleanup)

The steps drive the IDE with the mouse and keyboard, and one browser runs
in one thread, so the tabs of one browser take turns: only sessions in
different browsers loop truly at the same time. One browser per session
gives full concurrency.

run_loops_in_concurrent_sessions() is the same flow with any loop
(common/concurrent_load_files.py, common/concurrent_background_jobs.py).
"""
import posixpath
import threading
import time

from common.api_ui_combo import launch_sessions_in_tabs, login_and_quit_sessions_named
from common.launch_already_created_sessions import close_launched_sessions
from common.parallel_browsers import (
    MAX_TABS_PER_BROWSER,
    BrowserProgress,
    free_port,
    run_in_parallel_browsers,
    split_across_browsers,
)
from common.perf_report import attach_allure_file
from common.session_naming_logic import browser_name_prefixes
from common.single_browser_cleanup import SingleBrowserCleanup
from common.step_log import log_step
from common.text_file_crud import (
    MAX_FAILURES_IN_A_ROW,
    STEP_TIMEOUT_MS,
    describe_loop,
    prepare_session,
    run_file_loop,
    text_file_folder,
)
from config.config import env

# The stage that lasts as long as the loops (BrowserProgress stage_limits)
LOOP_STAGE = "running the loops"


def run_file_crud_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                         browser_count, label, duration_s, wait_s, stagger_s=0, attempts=3,
                                         launch_timeout_ms=180000, retry_wait_s=15):
    """Loop create -> open -> edit -> delete on a text file in every one of
    `session_names`, spread over `browser_count` browsers (started
    `stagger_s` apart), from the moment all are ready until `duration_s`
    later, waiting `wait_s` after each delete.

    A session that fails MAX_FAILURES_IN_A_ROW loops in a row stops looping;
    the others carry on. Returns (records, errors): a run record per step
    (common/text_file_crud.py) and "browser <B>: <error>" per browser with
    a failed loop or launch.
    """
    run_stamp = time.strftime("%Y%m%d-%H%M%S")

    def _prepare(session):
        prepare_session(session.page, session.session_name)
        log_step("Session %s ready for the file loops (folder %s)", session.session_name, text_file_folder())

    def _run_loops(launched, records):
        return _loop_sessions(launched, run_stamp, duration_s, wait_s, records)

    # one loop can take a few step timeouts on top of the duration
    loop_slack_s = 4 * 3 * STEP_TIMEOUT_MS / 1000.0
    return run_loops_in_concurrent_sessions(
        browser_name, launch_args, context_args, user, session_names, browser_count, label,
        "text file loops", _prepare, _run_loops, duration_s + loop_slack_s, stagger_s=stagger_s,
        attempts=attempts, launch_timeout_ms=launch_timeout_ms, retry_wait_s=retry_wait_s,
    )


def run_loops_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                     browser_count, label, what, prepare, run_loops, loop_stage_s,
                                     stagger_s=0, attempts=3, launch_timeout_ms=180000, retry_wait_s=15,
                                     start_together=True, with_home_url=False):
    """Launch `session_names` over `browser_count` browsers (started
    `stagger_s` apart), prepare(session) each, wait for all browsers, then
    run_loops(launched, records) in every browser - it appends its run
    records and returns its failures - and clean up. `loop_stage_s` is the
    most the loops may take; `what` names them in the log. With
    start_together=False each browser runs its loops as soon as its own
    sessions are ready (batch execution); cleanup still waits for all.
    With with_home_url=True it is run_loops(launched, records, home_url),
    home_url being the browser's session list.

    Returns (records, errors): the run records and "browser <B>: <error>"
    per browser with a failed loop or launch.
    """
    log_step("Starting %s: %s in %d session(s) over %d browser(s), as user %d",
             label, what, len(session_names), browser_count, user)
    prefixes = browser_name_prefixes(browser_count)
    names_by_browser = dict(zip(prefixes, split_across_browsers(session_names, browser_count)))
    for prefix, names in names_by_browser.items():
        print("\n[rstudio-local] browser %s: %d session(s) - %s" % (prefix.rstrip("_"), len(names), ", ".join(names)))
        if len(names) > 1:
            print("\n[rstudio-local] note: browser %s has %d tabs - they take turns, so its sessions do not "
                  "loop at the same time as each other" % (prefix.rstrip("_"), len(names)))
        if len(names) > MAX_TABS_PER_BROWSER:
            print("\n[rstudio-local] warning: browser %s has %d tabs, over %d - tabs may stall; use more browsers"
                  % (prefix.rstrip("_"), len(names), MAX_TABS_PER_BROWSER))

    ready = threading.Barrier(browser_count)
    debug_ports = {p: free_port() for p in prefixes} if browser_name == "chromium" else {}
    progress = BrowserProgress(prefixes, label=label, debug_ports=debug_ports,
                               stage_limits={LOOP_STAGE: loop_stage_s})
    launch_failed = set()
    cleanup = SingleBrowserCleanup(prefixes, user, session_names, launch_args, context_args)
    cleanup_wait_s = int(env("CONCURRENT_CLEANUP_WAIT_S") or (
        loop_stage_s + attempts * (launch_timeout_ms / 1000.0 + retry_wait_s)
        + stagger_s * browser_count + 300))

    def _worker(context, prefix):
        return _run_loops_in_browser(
            context, prefix, names_by_browser[prefix], user, ready, progress, launch_failed, attempts,
            launch_timeout_ms, retry_wait_s, what, prepare, run_loops, cleanup_wait_s, cleanup, start_together,
            with_home_url,
        )

    results = run_in_parallel_browsers(
        browser_name, launch_args, context_args, prefixes, _worker, barriers=(ready, progress),
        stagger_s=stagger_s, join_timeout_s=stagger_s * browser_count + cleanup_wait_s * 2 + 900,
        debug_ports=debug_ports, on_closed=cleanup.browser_closed,
    )
    cleanup.run_if_missed(browser_name, launch_args, context_args)
    records, errors = [], []
    for prefix, result, error in results:
        if result is not None:
            browser_records, error = result
            records += browser_records
        else:
            # a browser that never returned (stuck) or died: its sessions go
            # into the results as failed
            if isinstance(error, TimeoutError):
                progress.capture([prefix])
            reason = ("browser stuck: %s" % progress.stage(prefix) if isinstance(error, TimeoutError)
                      else "browser failed: %s" % " ".join(str(error).split())[:150])
            seen = {r["session_name"] for r in records}
            records += [{"session_name": name, "started": None, "ended": None, "status": reason}
                        for name in names_by_browser[prefix] if name not in seen]
        if error is not None:
            errors.append("browser %s: %s" % (prefix.rstrip("_"), error))
    for path in progress.captured:
        attach_allure_file(path)
    return records, errors


def _run_loops_in_browser(context, prefix, session_names, user, ready, progress, launch_failed, attempts,
                          launch_timeout_ms, retry_wait_s, what, prepare, run_loops, cleanup_wait_s, cleanup,
                          start_together=True, with_home_url=False):
    """One browser's part of run_loops_in_concurrent_sessions(). Returns
    (records, error): its run records and the error that failed it, else None."""
    where = " in browser %s" % prefix.rstrip("_")
    launched, records, error = [], [], None
    try:
        try:
            progress.set(prefix, "logging in")
            home_url, before_ids = login_and_quit_sessions_named([context], user, session_names)
            cleanup.note_login(prefix, home_url, before_ids)
            progress.set(prefix, "launching sessions")
            launched, failed = launch_sessions_in_tabs(
                context, home_url, session_names, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
                retry_wait_s=retry_wait_s, where=where,
            )
            assert not failed, "sessions that failed to launch: %s" % failed
            progress.set(prefix, "preparing the sessions")
            for session in launched:
                prepare(session)
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
                raise AssertionError("loops skipped: launch failed in browser(s) %s"
                                     % sorted(p.rstrip("_") for p in launch_failed))
            log_step("All browsers are ready - running the %s", what)
        else:
            log_step("%d session(s) ready in this browser - running the %s", len(launched), what)
        progress.set(prefix, LOOP_STAGE)
        failures = run_loops(launched, records, home_url) if with_home_url else run_loops(launched, records)
        assert not failures, "loops that failed: %s" % "; ".join(failures)
    except Exception as exc:
        error = exc
    finally:
        log_step("This browser is done - waiting for the other browsers to finish before cleanup")
        if not progress.done_and_wait(prefix, cleanup_wait_s) and error is None:
            error = AssertionError("cleaned up without waiting for browser(s) still not done after %ds: %s"
                                   % (cleanup_wait_s, progress.pending()))
        close_launched_sessions(launched)
        cleanup.run(context, prefix)
    return records, error


def _loop_sessions(launched, run_stamp, duration_s, wait_s, records):
    """Loop every session in `launched` until `duration_s` from now: each
    starts a loop as soon as it is due (at once, then `wait_s` after its
    last delete), one tab at a time. Appends the step records to `records`;
    returns "<session> loop <n>: <error>" per failed loop."""
    loops = FileLoops(launched, run_stamp, time.time() + duration_s, wait_s, records)
    while True:
        wait = loops.tick()
        if wait is None:
            return loops.failures
        time.sleep(wait)


class FileLoops:
    """The file loops of _loop_sessions(), one at a time: each tick() runs
    the loop of the session most overdue (if any) and returns the seconds
    until the next one is due, or None once no session will loop again.
    Lets a caller drive other tabs of the same browser between loops
    (common/concurrent_scenario.py). Step records go to `records`, failed
    loops to `failures`."""

    def __init__(self, launched, run_stamp, deadline, wait_s, records):
        self.run_stamp = run_stamp
        self.deadline = deadline
        self.wait_s = wait_s
        self.records = records
        self.failures = []
        self.state = {s.session_name: {"due": time.time(), "loop": 0, "in_a_row": 0} for s in launched}
        self.active = list(launched)

    def tick(self):
        now = time.time()
        if now >= self.deadline:
            self.active = []  # another tab's loop ran past the end: no new loops
        if not self.active:
            return None
        due = [s for s in self.active if self.state[s.session_name]["due"] <= now]
        if due:
            self._run_one(min(due, key=lambda s: self.state[s.session_name]["due"]))
        if not self.active:
            return None
        return max(0.0, min(self.state[s.session_name]["due"] for s in self.active) - time.time())

    def _run_one(self, session):
        st = self.state[session.session_name]
        st["loop"] += 1
        path = posixpath.join(text_file_folder(), "text_file_%s_%s_%d.txt"
                              % (self.run_stamp, session.session_name, st["loop"]))
        session.page.bring_to_front()  # keyboard input goes to the tab in front
        loop_records, error = run_file_loop(session.page, session.session_name, path, st["loop"])
        self.records += loop_records
        if error:
            st["in_a_row"] += 1
            self.failures.append("%s loop %d: %s" % (session.session_name, st["loop"], error))
            log_step("Session %s loop %d: %s", session.session_name, st["loop"], error)
        else:
            st["in_a_row"] = 0
            log_step("Session %s loop %d ok: %s", session.session_name, st["loop"], describe_loop(loop_records))
        st["due"] = time.time() + self.wait_s
        if st["in_a_row"] >= MAX_FAILURES_IN_A_ROW:
            log_step("Session %s: %d loops failed in a row - it stops looping", session.session_name, st["in_a_row"])
            self.active.remove(session)
        elif st["due"] >= self.deadline:
            self.active.remove(session)  # its next loop would start after the duration is up
