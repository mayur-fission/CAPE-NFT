"""Helpers for tests/test_concurrent_sessions.py: launch N sessions named
CONCURRENT_SESSION_1 .. CONCURRENT_SESSION_<N> from the UI, spread over
several browsers with a handful of tabs each, source one R script in all of
them at the same moment and time every run.

Each browser runs in its own thread (run_in_parallel_browsers()), logs in
as the same user and only touches its own sessions. Browsers wait for each
other before the scripts start, so the runs overlap, and before cleanup.

Run records use the common.script_timings format.
"""
import base64
import json
import math
import os
import socket
import threading
import time
import urllib.request

from config.perf_report import attach_allure_file, write_json_report

from common.api_ui_combo import (
    browser_name_prefixes,
    launch_sessions_in_tabs,
    login_and_quit_sessions_named,
    quit_ui_sessions_named,
    report_failed_runs,
    run_in_parallel_browsers,
)
from common.config import EVIDENCE_DIR, env, env_path
from common.launch_already_created_sessions import (
    SETWD_ATTEMPTS,
    SETWD_RETRY_WAIT_S,
    close_launched_sessions,
    run_script_in_launched_sessions,
)
from common.session_actions import set_session_working_directory
from common.step_log import log_step

CONCURRENT_SESSION_PREFIX = "CONCURRENT_SESSION_"
# Every IDE tab keeps a long-poll connection open, and Chrome allows 6
# HTTP/1.1 connections per host: more tabs than this in one browser queue
# their requests and stall (see create_sessions_in_ui_from_csvs()).
MAX_TABS_PER_BROWSER = 6
DEFAULT_TABS_PER_BROWSER = 5
# Working directory set in every session right after it launches; the
# script itself then runs in its own <output_dir>/<session name> folder.
SESSION_HOME_DIR = env_path("WORKBENCH_HOME_DIR", "CONCURRENT_SESSIONS_HOME_DIR")


# How long a browser may stay in one stage, while other browsers are already
# done, before its tabs are captured as possibly stuck (screenshots + URLs).
STUCK_CAPTURE_S = int(env("CONCURRENT_STUCK_CAPTURE_S", "600"))


def _free_port():
    """A free local TCP port for a browser's DevTools endpoint."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _screenshot_target(ws_url, path, timeout_s=15):
    """Page.captureScreenshot over the tab's own DevTools websocket. Raises
    on failure (a frozen tab does not answer: timeout)."""
    import websocket  # websocket-client (requirements.txt)
    ws = websocket.create_connection(ws_url, timeout=timeout_s, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                break
    finally:
        ws.close()
    if "result" not in msg:
        raise RuntimeError(msg.get("error"))
    with open(path, "wb") as f:
        f.write(base64.b64decode(msg["result"]["data"]))


def capture_stuck_browser(port, prefix, label, stage, since, timeout_s=120):
    """Capture a browser whose own thread is stuck, through its DevTools
    port: the tab list (URL and title of every tab - the browser process
    answers it even when a tab is frozen) and a screenshot of each tab over
    that tab's own DevTools connection (a frozen tab cannot render one, so it
    gets the error instead). Playwright's connect_over_cdp() is not used: it
    initialises every tab first and so hangs on the frozen one.

    Writes evidence/stuck_<label>_<browser>_<time>.json (stage, for how
    long, the tabs) and stuck_..._tab<n>_<time>.png. Without websocket-client
    only the tab list is saved. Gives up after timeout_s. Returns the paths
    written."""
    browser_id = prefix.rstrip("_")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    info = {"browser": browser_id, "stage": stage, "stuck_for_s": round(time.time() - since),
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "tabs": []}
    paths = []

    def _run():
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=15) as resp:
                targets = [t for t in json.load(resp) if t.get("type") == "page"]
        except Exception as exc:
            info["error"] = "could not list the tabs: %s" % " ".join(str(exc).split())[:200]
            return
        try:
            import websocket  # noqa: F401 - only to check it is installed
            can_screenshot = True
        except ImportError:
            can_screenshot = False
            info["note"] = "websocket-client not installed - tab list only, no screenshots"
        for n, target in enumerate(targets, 1):
            tab = {"tab": n, "title": target.get("title"), "url": target.get("url")}
            if can_screenshot and target.get("webSocketDebuggerUrl"):
                path = os.path.join(EVIDENCE_DIR, "stuck_%s_%s_tab%d_%s.png" % (label, browser_id, n, stamp))
                try:
                    _screenshot_target(target["webSocketDebuggerUrl"], path)
                    tab["screenshot"] = os.path.basename(path)
                    paths.append(path)
                except Exception as exc:
                    tab["screenshot_error"] = (" ".join(str(exc).split())[:200]
                                               or "no answer - the tab looks frozen")
            info["tabs"].append(tab)

    thread = threading.Thread(target=_run, name="capture-%s" % browser_id, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        info["error"] = "capture did not finish within %ds" % timeout_s
    json_path = os.path.join(EVIDENCE_DIR, "stuck_%s_%s_%s.json" % (label, browser_id, stamp))
    write_json_report(json_path, info)
    log_step("Captured possibly stuck browser %s (%s for %ds): %d tab(s), %d screenshot(s)%s -> %s",
             browser_id, stage, info["stuck_for_s"], len(info["tabs"]), len(paths),
             " - " + info["error"] if "error" in info else "", json_path)
    return [json_path] + paths


class _BrowserProgress:
    """What each browser of a concurrent run is doing, and the wait before
    cleanup: every browser waits until all of them are done (or the wait
    times out). While it waits, the first browser that finished logs, every
    report_every_s, which browsers it is still waiting for and what they are
    doing, and captures (capture_stuck_browser()) any that has stayed in one
    stage for STUCK_CAPTURE_S - and every one still not done when the wait
    gives up. Captured files are collected in `captured` for the caller to
    attach to Allure from the test's own thread."""

    def __init__(self, prefixes, label="", debug_ports=None, report_every_s=60):
        self._cond = threading.Condition()
        self._stage = {p: ("starting", time.time()) for p in prefixes}
        self._done = set()
        self._aborted = False
        self._reporter = None
        self._report_every_s = report_every_s
        self._label = label
        self._ports = debug_ports or {}
        self._captured_stage = {}   # prefix -> the stage it was captured in
        self.captured = []          # evidence files written by the captures

    def set(self, prefix, stage):
        with self._cond:
            self._stage[prefix] = (stage, time.time())

    def stage(self, prefix):
        with self._cond:
            return self._stage[prefix][0]

    def abort(self):
        """A browser thread died outside its worker: stop waiting for it."""
        with self._cond:
            self._aborted = True
            self._cond.notify_all()

    def pending(self):
        """'B24 (launching sessions, 7 min), ...' for the browsers not done."""
        now = time.time()
        return ", ".join(
            "%s (%s, %d min)" % (p.rstrip("_"), stage, (now - since) // 60)
            for p, (stage, since) in sorted(self._stage.items(), key=lambda kv: int(kv[0][1:-1]))
            if p not in self._done
        )

    def capture(self, prefixes):
        """Capture each of `prefixes` (not done, not yet captured in its
        current stage) that has a DevTools port."""
        for prefix in prefixes:
            with self._cond:
                stage, since = self._stage[prefix]
                if prefix in self._done or self._captured_stage.get(prefix) == stage:
                    continue
                self._captured_stage[prefix] = stage
            if prefix in self._ports:
                self.captured += capture_stuck_browser(self._ports[prefix], prefix, self._label, stage, since)

    def done_and_wait(self, prefix, timeout_s):
        """Mark `prefix` done, then wait up to timeout_s for the others.
        Returns True when all are done, False on timeout."""
        deadline = time.time() + timeout_s
        with self._cond:
            self._done.add(prefix)
            self._stage[prefix] = ("done", time.time())
            if self._reporter is None:
                self._reporter = prefix
            self._cond.notify_all()
        last_report = time.time()
        while True:
            with self._cond:
                if len(self._done) == len(self._stage) or self._aborted:
                    return True
                left = deadline - time.time()
                if left <= 0:
                    pending = [p for p in self._stage if p not in self._done]
                    log_step("Gave up waiting after %ds - cleaning up anyway; still not done: %s",
                             timeout_s, self.pending())
                    reporter = prefix == self._reporter
                    break
                self._cond.wait(min(self._report_every_s, left))
                reporter = prefix == self._reporter
                now = time.time()
                slow = [p for p, (_, since) in self._stage.items()
                        if p not in self._done and now - since >= STUCK_CAPTURE_S]
                report = (reporter and len(self._done) < len(self._stage)
                          and now - last_report >= self._report_every_s)
                if report:
                    log_step("Still waiting for %d browser(s) before cleanup: %s",
                             len(self._stage) - len(self._done), self.pending())
                    last_report = now
            if reporter and slow:
                self.capture(slow)   # outside the lock: a capture takes up to a couple of minutes
        if reporter:
            self.capture(pending)
        return False


def concurrent_session_names(count, prefix=CONCURRENT_SESSION_PREFIX):
    """[<prefix>1, ..., <prefix><count>]"""
    return ["%s%d" % (prefix, i + 1) for i in range(count)]


def browsers_for(session_count, browser_count=None, tabs_per_browser=DEFAULT_TABS_PER_BROWSER):
    """`browser_count` if given (> 0), else enough browsers for
    `tabs_per_browser` sessions each. Never more browsers than sessions."""
    count = browser_count or math.ceil(session_count / float(tabs_per_browser))
    return max(1, min(count, session_count))


def split_across_browsers(session_names, browser_count):
    """`session_names` in `browser_count` consecutive chunks whose sizes
    differ by at most one (11 names over 2 browsers -> 6 and 5)."""
    base, extra = divmod(len(session_names), browser_count)
    chunks, start = [], 0
    for i in range(browser_count):
        size = base + (1 if i < extra else 0)
        chunks.append(session_names[start:start + size])
        start += size
    return chunks


def run_script_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                      browser_count, label, script_path, output_dir, output_file_name,
                                      script_timeout_ms, stagger_s=0, attempts=3, launch_timeout_ms=180000,
                                      retry_wait_s=15, delete_output=False, poll_interval_ms=500,
                                      start_together=True):
    """Spread `session_names` over `browser_count` browsers (see
    split_across_browsers()). Each browser logs in as `user`, quits leftover
    sessions with its names, launches each name in its own tab, waits for
    the other browsers (only with start_together - otherwise each browser
    starts as soon as its own sessions are launched, "batch" mode), then
    sources `script_path` in all its tabs at once
    (each in `output_dir`/<session name>, emptied first; a run only counts
    as ok if `output_file_name` is there afterwards) and waits up to
    `script_timeout_ms`. Browsers start `stagger_s` apart. With
    delete_output, each session's output (file or folder) is deleted once
    it has been checked, so large outputs do not pile up on the server.
    Each console is checked every `poll_interval_ms` while the scripts run.

    With start_together, the scripts are skipped everywhere if any browser
    failed to launch its sessions; in batch mode a browser whose launch
    failed is reported and the others carry on. Either way, every browser
    waits for the others to finish their scripts (and output checks) before
    it closes its tabs and quits its sessions, so no cleanup runs while
    another browser is still executing. Tabs are closed and the created
    sessions quit even on failure.

    Returns (runs, errors): every run record, and "browser <prefix>: <error>"
    per failed browser.
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
    debug_ports = {p: _free_port() for p in prefixes} if browser_name == "chromium" else {}
    progress = _BrowserProgress(prefixes, label=label, debug_ports=debug_ports)
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
            start_together, cleanup_wait_s,
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
                           start_together=True, cleanup_wait_s=3600):
    """One browser's part of run_script_in_concurrent_sessions(); a browser
    whose launch fails adds its prefix to `launch_failed`. Each stage is
    recorded in `progress` (a _BrowserProgress), which also holds the wait
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
                        break
                    except Exception as exc:
                        if attempt == SETWD_ATTEMPTS:
                            raise
                        log_step("Could not set the working directory in session %s (attempt %d of %d), "
                                 "retrying in %ds: %s", session.session_name, attempt, SETWD_ATTEMPTS,
                                 SETWD_RETRY_WAIT_S, exc)
                        session.page.wait_for_timeout(SETWD_RETRY_WAIT_S * 1000)
                log_step("Working directory set to %s in session %s", SESSION_HOME_DIR, session.session_name)
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
            quit_ui_sessions_named(context, home_url, before_ids, session_names, "browser %s" % prefix.rstrip("_"))
    return runs, error


def summarize_concurrent_runs(runs, label, session_count, browser_count):
    """Print and save the time the script took across all sessions: from
    the first start to the last finish (wall clock), plus the fastest,
    average and slowest run among those that finished ok. Written to
    evidence/rstudio_concurrent_summary_<label>.json and attached to Allure.
    Returns the summary dict.
    """
    ok = [r for r in runs if r["status"] == "ok" and r["started"] is not None and r["ended"] is not None]
    durations = sorted(r["ended"] - r["started"] for r in ok)
    started = [r["started"] for r in runs if r["started"] is not None]
    ended = [r["ended"] for r in ok]
    summary = {
        "label": label,
        "sessions": session_count,
        "browsers": browser_count,
        "runs": len(runs),
        "ok": len(ok),
        "failed": len(runs) - len(ok),
        "wall_clock_s": round(max(ended) - min(started), 2) if ended and started else None,
        "min_s": round(durations[0], 2) if durations else None,
        "avg_s": round(sum(durations) / len(durations), 2) if durations else None,
        "max_s": round(durations[-1], 2) if durations else None,
    }
    print(
        "\n[rstudio-local] %s: %d of %d session(s) ran the script ok across %d browser(s); "
        "all sessions took %ss (first start to last finish), per session min %ss / avg %ss / max %ss"
        % (label, summary["ok"], summary["runs"], browser_count, summary["wall_clock_s"],
           summary["min_s"], summary["avg_s"], summary["max_s"])
    )
    path = os.path.join(EVIDENCE_DIR, "rstudio_concurrent_summary_%s.json" % label)
    write_json_report(path, summary)
    attach_allure_file(path)
    return summary
