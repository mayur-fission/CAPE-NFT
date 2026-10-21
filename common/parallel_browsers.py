"""Several browsers at once, each in its own thread with its own Playwright,
plus what keeps them in step: per-browser progress, the wait for all
before cleanup (with a time limit and progress report) and capture of a
stuck browser (tab list + screenshots over DevTools).
"""
import time
from playwright.sync_api import sync_playwright
from common.step_log import log_step
import threading
from config.config import env
import socket
import json
import base64
import urllib
import os
from config.config import evidence_dir
from common.perf_report import write_json_report
import math
import signal
import subprocess

# Longest browser.close() may take before the browser process is killed: a
# frozen Chromium never answers the close, and closing its window by hand
# does not end the process, so the test would wait for ever.
BROWSER_CLOSE_TIMEOUT_S = int(env("BROWSER_CLOSE_TIMEOUT_S", "60"))


def run_in_parallel_browsers(browser_name, launch_args, context_args, prefixes, worker, barriers=(),
                             stagger_s=0, join_timeout_s=None, debug_ports=None, on_closed=None):
    """Open one browser per prefix, each in its own thread with its own
    Playwright, `stagger_s` apart, and call worker(context, prefix) there.
    Returns [(prefix, result, error)] in prefix order.

    A thread failing aborts `barriers` so the others don't wait for ever.
    join_timeout_s: stop waiting for a frozen browser (reported as
    TimeoutError). debug_ports {prefix: port}: Chromium listens for DevTools
    there, so a stuck browser can be captured from another thread.
    on_closed(prefix) is called in each browser's thread once its browser
    has closed (or failed to start).
    """
    results = [None] * len(prefixes)

    def _run(index, prefix):
        try:
            if index and stagger_s:
                print("\n[rstudio-local] browser %s: starting in %ds" % (prefix, index * stagger_s))
                time.sleep(index * stagger_s)
            args = dict(launch_args)
            if debug_ports and prefix in debug_ports and browser_name == "chromium":
                args["args"] = list(args.get("args") or []) + ["--remote-debugging-port=%d" % debug_ports[prefix]]
            with sync_playwright() as playwright:
                browser = getattr(playwright, browser_name).launch(**args)
                log_step("Browser %s started (%s)", prefix.rstrip("_"), browser_name)
                try:
                    context = browser.new_context(**{**context_args, "ignore_https_errors": True})
                    results[index] = (prefix, worker(context, prefix), None)
                finally:
                    close_browser(browser, prefix, (debug_ports or {}).get(prefix))
                    log_step("Browser %s closed", prefix.rstrip("_"))
        except BaseException as exc:
            print("\n[rstudio-local] browser %s failed: %s" % (prefix, exc))
            results[index] = (prefix, None, exc)
            for barrier in barriers:
                barrier.abort()
        finally:
            if on_closed is not None:
                on_closed(prefix)

    threads = [
        threading.Thread(target=_run, args=(i, prefix), name="browser-%s" % prefix.rstrip("_"),
                         daemon=join_timeout_s is not None)
        for i, prefix in enumerate(prefixes)
    ]
    for thread in threads:
        thread.start()
    deadline = time.time() + join_timeout_s if join_timeout_s is not None else None
    for i, thread in enumerate(threads):
        thread.join(None if deadline is None else max(0, deadline - time.time()))
        if thread.is_alive():
            print("\n[rstudio-local] browser %s still running after %ds - not waiting for it any longer"
                  % (prefixes[i], join_timeout_s))
            results[i] = (prefixes[i], None, TimeoutError(
                "browser did not finish within %ds (stuck - its sessions may need deleting by hand)"
                % join_timeout_s))
    return results


def wait_until_epoch(start_at_epoch, who="this machine"):
    """Sleep until `start_at_epoch` (Unix time, number or string), logging
    the start time for `who`; no wait if it is blank or past."""
    if not start_at_epoch:
        return
    start_at = float(start_at_epoch)
    wait_s = start_at - time.time()
    print("\n[rstudio-local] %s: starting at %s (in %.0fs)"
          % (who, time.strftime("%H:%M:%S", time.localtime(start_at)), max(0.0, wait_s)))
    if wait_s > 0:
        time.sleep(wait_s)


# --- Progress, the wait before cleanup, and stuck-browser capture --------------


# Every IDE tab keeps a long-poll connection open, and Chrome allows 6
# HTTP/1.1 connections per host: more tabs than this in one browser queue
# their requests and stall (see create_sessions_in_ui_from_csvs()).
MAX_TABS_PER_BROWSER = 6


DEFAULT_TABS_PER_BROWSER = 5


# How long a browser may stay in one stage, while other browsers are already
# done, before its tabs are captured as possibly stuck (screenshots + URLs).
STUCK_CAPTURE_S = int(env("CONCURRENT_STUCK_CAPTURE_S", "600"))


def close_browser(browser, prefix, debug_port=None, timeout_s=BROWSER_CLOSE_TIMEOUT_S):
    """browser.close(), killing the browser process (found by its DevTools
    `debug_port`) if it has not closed within `timeout_s` - which makes the
    close return. Without a port (not Chromium) it is a plain close."""
    timer = None
    if debug_port:
        def _kill():
            log_step("Browser %s did not close within %ds (frozen?) - killing its process",
                     prefix.rstrip("_"), timeout_s)
            try:
                kill_browser_on_port(debug_port)
            except Exception as exc:
                log_step("Could not kill browser %s: %s", prefix.rstrip("_"), exc)

        timer = threading.Timer(timeout_s, _kill)
        timer.daemon = True
        timer.start()
    try:
        browser.close()
    finally:
        if timer is not None:
            timer.cancel()


def kill_browser_on_port(port):
    """Kill the Chromium started with --remote-debugging-port=<port>; its
    tab processes exit with it. On Linux the process is found in /proc (no
    pkill needed, e.g. in the Jenkins container)."""
    flag = "--remote-debugging-port=%d" % port
    if os.name == "nt":
        command = ["powershell", "-NoProfile", "-Command",
                   "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
                   "$_.CommandLine -match '%s(\\s|$)' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
                   % flag]
        subprocess.run(command, capture_output=True, timeout=60)
        return
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                args = f.read().decode(errors="replace").split("\0")
            if flag in args:
                os.kill(int(pid), signal.SIGKILL)
        except (OSError, ValueError):
            pass  # gone meanwhile, or not ours to read


def free_port():
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
    """Capture a browser whose own thread is stuck, over its DevTools port:
    the tab list (URL, title - answered even when a tab is frozen) and a
    screenshot per tab (a frozen tab gets the error instead). Not
    connect_over_cdp(): it hangs on the frozen tab.

    Writes evidence stuck_<label>_<browser>_<time>.json/.png (tab list only
    without websocket-client). Returns the paths written.
    """
    browser_id = prefix.rstrip("_")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = evidence_dir()  # the running test's folder
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
                path = os.path.join(folder, "stuck_%s_%s_tab%d_%s.png" % (label, browser_id, n, stamp))
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
    json_path = os.path.join(folder, "stuck_%s_%s_%s.json" % (label, browser_id, stamp))
    write_json_report(json_path, info)
    log_step("Captured possibly stuck browser %s (%s for %ds): %d tab(s), %d screenshot(s)%s -> %s",
             browser_id, stage, info["stuck_for_s"], len(info["tabs"]), len(paths),
             " - " + info["error"] if "error" in info else "", json_path)
    return [json_path] + paths


class BrowserProgress:
    """What each browser of a concurrent run is doing, and the wait before
    cleanup: every browser waits until all of them are done (or the wait
    times out). While it waits, the first browser that finished logs, every
    report_every_s, which browsers it is still waiting for and what they are
    doing, and captures (capture_stuck_browser()) any that has stayed in one
    stage for STUCK_CAPTURE_S - and every one still not done when the wait
    gives up, after which those are killed (kill()). Captured files are
    collected in `captured` for the caller to attach to Allure from the
    test's own thread.

    stage_limits {stage: seconds}: stages that legitimately take longer
    (e.g. a 10-minute script) count as stuck only after that long, so a
    browser that is simply still running is not captured;
    CONCURRENT_STUCK_CAPTURE_S still applies when it is the larger."""

    def __init__(self, prefixes, label="", debug_ports=None, report_every_s=60, stage_limits=None):
        self._cond = threading.Condition()
        self._stage = {p: ("starting", time.time()) for p in prefixes}
        self._done = set()
        self._aborted = False
        self._reporter = None
        self._report_every_s = report_every_s
        self._label = label
        self._ports = debug_ports or {}
        self._captured_stage = {}   # prefix -> the stage it was captured in
        self._stage_limits = dict(stage_limits or {})
        self.captured = []          # evidence files written by the captures

    def stuck_after(self, stage):
        """Seconds a browser may stay in `stage` before it counts as stuck."""
        return max(STUCK_CAPTURE_S, self._stage_limits.get(stage, 0))

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
                slow = [p for p, (stage, since) in self._stage.items()
                        if p not in self._done and now - since >= self.stuck_after(stage)]
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
            self.kill(pending)
        return False

    def kill(self, prefixes):
        """Kill each of `prefixes` that is still not done and has a DevTools
        port (kill_browser_on_port()): its stuck Playwright call then fails,
        so its thread goes on to clean up (quitting its sessions from a new
        browser) instead of hanging the test until the join timeout."""
        for prefix in prefixes:
            with self._cond:
                if prefix in self._done:
                    continue
                stage = self._stage[prefix][0]
            if prefix not in self._ports:
                log_step("Browser %s is still stuck (%s) and has no DevTools port - cannot kill it",
                         prefix.rstrip("_"), stage)
                continue
            log_step("Killing stuck browser %s (%s) so its thread can clean up", prefix.rstrip("_"), stage)
            try:
                kill_browser_on_port(self._ports[prefix])
            except Exception as exc:
                log_step("Could not kill browser %s: %s", prefix.rstrip("_"), exc)


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
