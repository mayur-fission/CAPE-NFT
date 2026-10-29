"""Sessions held idle in their tabs: sessions spread over browsers (5 tabs
each), all logged in as one user (common/concurrent_file_crud.py's flow).
Once every browser has launched its sessions, each tab is left open with
nothing run in it until the duration is up. Every `check_interval_s` each
browser checks its sessions for drops, two ways:
  - the tab: the session's IDE no longer shows its Console tab
  - the session list (in a separate tab): the session left the list or is
    no longer Active/Executing (e.g. Suspended)
A dropped session is no longer checked. The sessions are then quit.

One record per session: "started" = idle start, "ended" = idle end (or
when the drop was seen). Every drop is logged as SESSION DROP with a
screenshot, every session list poll goes to
evidence/session_monitor_<label>_<first session of the browser>.csv, and
the drops to evidence/session_drops_<label>.json, all attached to Allure.
"""
import csv
import os
import threading
import time

from common.concurrent_file_crud import run_loops_in_concurrent_sessions
from common.evidence import save_screenshot
from common.perf_report import attach_allure_file, write_json_report
from common.rstudio_console_commands import wait_for_console_ready
from common.rstudio_session_helper import ACTIVE_STATUSES, goto_session_list, is_session_alive, load_all_session_rows
from common.session_monitor import GONE, session_statuses_by_name
from common.step_log import log_step
from config.config import env, evidence_dir

IDLE_SCRIPT = "idle"
# Time allowed for the last check, on top of the duration.
IDLE_GRACE_S = float(env("CONCURRENT_IDLE_GRACE_S", "120"))


def run_idle_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                    browser_count, label, duration_s, check_interval_s=60, stagger_s=0,
                                    attempts=3, launch_timeout_ms=180000, retry_wait_s=15):
    """Keep every one of `session_names` open and idle in its tab, spread
    over `browser_count` browsers (started `stagger_s` apart), from the
    moment all are ready until `duration_s` later, watching for drops.

    Returns (records, drops, errors): a record per session, a
    {session, status, detected_by, seconds_after_idle_start, time} dict per
    dropped session, and "browser <B>: <error>" per browser with a drop or
    failed launch.
    """
    drops, drops_lock = [], threading.Lock()

    def _prepare(session):
        wait_for_console_ready(session.page)
        log_step("Session %s ready - it will be left idle", session.session_name)

    def _hold_idle(launched, records, home_url):
        browser_drops = _watch_idle_sessions(launched, records, home_url, duration_s, check_interval_s,
                                             "%s_%s" % (label, launched[0].session_name))
        with drops_lock:
            drops.extend(browser_drops)
        return ["%s: %s" % (d["session"], d["status"]) for d in browser_drops]

    records, errors = run_loops_in_concurrent_sessions(
        browser_name, launch_args, context_args, user, session_names, browser_count, label,
        "idle hold (for %.0fs)" % duration_s, _prepare, _hold_idle, duration_s + IDLE_GRACE_S,
        stagger_s=stagger_s, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
        retry_wait_s=retry_wait_s, with_home_url=True,
    )
    drops.sort(key=lambda d: d["seconds_after_idle_start"])
    if drops:
        json_path = os.path.join(evidence_dir(), "session_drops_%s.json" % label)
        write_json_report(json_path, drops)
        attach_allure_file(json_path)
    return records, drops, errors


def _watch_idle_sessions(launched, records, home_url, duration_s, check_interval_s, monitor_label):
    """One browser's idle hold: append a record per session in `launched`
    to `records`, check them every `check_interval_s` until `duration_s` is
    up, and return this browser's drops."""
    log_step("Holding %d session(s) idle for %.0fs, checking for drops every %.0fs",
             len(launched), duration_s, check_interval_s)
    watch = IdleWatch(launched, records, home_url, time.time() + duration_s, check_interval_s, monitor_label)
    try:
        while True:
            wait = watch.tick()
            if wait is None:
                break
            watch.home_page.wait_for_timeout(wait * 1000)
    finally:
        watch.finish()
    return watch.drops


class IdleWatch:
    """Drop checks of idle sessions, one round per tick(): every
    `check_interval_s` until `deadline`, each still-watched session's tab
    and its row on the session list (read in a tab of its own). tick()
    returns the seconds until the next round, or None once the deadline has
    passed or every session has dropped; finish() ends the records and
    closes the session list tab. Lets a caller drive other tabs of the same
    browser between rounds (common/concurrent_scenario.py)."""

    def __init__(self, launched, records, home_url, deadline, check_interval_s, monitor_label):
        self.started = time.time()
        self.deadline = deadline
        self.check_interval_s = check_interval_s
        self.home_url = home_url
        self.idle = {s.session_name: {"session_name": s.session_name, "started": self.started, "ended": None,
                                      "status": "ok", "script": IDLE_SCRIPT} for s in launched}
        records += self.idle.values()
        self.names = list(self.idle)
        self.total = len(launched)
        self.watching = list(launched)
        self.drops = []
        self.done = False
        self.next_check = min(self.started + check_interval_s, deadline)
        self.csv_path = os.path.join(evidence_dir(), "session_monitor_%s.csv" % monitor_label)
        self._file = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["Time", "Seconds since idle start"] + self.names)
        self.home_page = launched[0].page.context.new_page()

    def tick(self):
        if self.done or not self.watching:
            return None
        now = time.time()
        if now >= self.next_check:
            self._check(now - self.started)
            if self.next_check >= self.deadline:
                self.done = True  # that was the last round, when the duration was up
                return None
            self.next_check = min(now + self.check_interval_s, self.deadline)
        return max(0.0, self.next_check - time.time())

    def _check(self, elapsed):
        for session in [s for s in self.watching if not _tab_alive(s)]:
            self.drops.append(_drop(self.idle[session.session_name], "IDE no longer shows its Console tab",
                                    "tab", elapsed, session.page))
            self.watching.remove(session)
        statuses = _session_list_statuses(self.home_page, self.home_url, self.names, elapsed)
        if statuses is None:
            return
        self._writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), round(elapsed)]
                              + [statuses.get(n, GONE) for n in self.names])
        self._file.flush()
        for session in [s for s in self.watching if statuses.get(s.session_name) not in ACTIVE_STATUSES]:
            self.drops.append(_drop(self.idle[session.session_name], statuses.get(session.session_name, GONE),
                                    "session list", elapsed, self.home_page))
            self.watching.remove(session)
        if self.watching:
            log_step("Idle check at %.0fs of %.0fs: %d of %d idle session(s) still alive",
                     elapsed, self.deadline - self.started, len(self.watching), self.total)

    def finish(self):
        try:
            self.home_page.close()
        except Exception:
            pass  # the browser went away
        self._file.close()
        attach_allure_file(self.csv_path)
        ended = time.time()
        for record in self.idle.values():
            if record["ended"] is None:
                record["ended"] = ended
        log_step("Idle time up - %d of %d idle session(s) dropped", len(self.drops), self.total)


def _tab_alive(session):
    try:
        return is_session_alive(session.page)
    except Exception:
        return False  # the tab itself is gone or hung


def _session_list_statuses(page, home_url, names, elapsed):
    """{name: status} from the session list, or None (logged, not a drop)
    when the list can't be read."""
    try:
        goto_session_list(page, home_url, settle_ms=1000)
        load_all_session_rows(page)
        return session_statuses_by_name(page, names)
    except Exception as exc:
        log_step("Idle check: could not read the session list at %.0fs: %s", elapsed, exc)
        return None


def _drop(record, status, detected_by, elapsed, page):
    """Mark `record` dropped, log it, screenshot `page`; returns the drop."""
    record["ended"] = time.time()
    record["status"] = "dropped (%s, seen in the %s) after %.0fs idle" % (status, detected_by, elapsed)
    name = record["session_name"]
    log_step("SESSION DROP: %s is %s (seen in the %s), %.0fs into the idle hold", name, status, detected_by, elapsed)
    try:
        save_screenshot(page, "session_drop_%s_%ds.png" % (name, elapsed), always=True)
    except Exception as exc:
        log_step("Could not screenshot the drop of %s: %s", name, exc)
    return {"session": name, "status": status, "detected_by": detected_by,
            "seconds_after_idle_start": round(elapsed), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
