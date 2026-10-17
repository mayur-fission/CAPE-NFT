"""Watch sessions left running without a tab, and stop their console scripts.

monitor_sessions() polls the session list while the sessions run on their
own and records every session's status; a session that leaves the list or
stops being Active/Executing (e.g. Suspended, Failed) is a drop: it is
logged, the session list is screenshotted and the drop goes to the
evidence. stop_console_scripts() reopens each session and interrupts its
running R script before the sessions are quit.
"""
import csv
import os
import time

from config.perf_report import attach_allure_file, write_json_report

from common import locators
from common.config import EVIDENCE_DIR
from common.evidence import save_screenshot
from common.rstudio_console_commands import CONSOLE_WAIT_TIMEOUT_MS, run_console_command
from common.rstudio_session_helper import (
    ACTIVE_STATUSES,
    goto_session_list,
    load_all_session_rows,
    open_existing_session,
)
from common.step_log import log_step

GONE = "gone from the session list"


def session_statuses_by_name(page, session_names):
    """{name: status} for the rows of the session list named one of
    `session_names`, read in one atomic call (the list updates live). A name
    without a row is left out. `page` must show the session list."""
    names = set(session_names)
    pairs = page.eval_on_selector_all(
        locators.SESSION_STATUS_CELL_SELECTOR,
        """els => els.map(el => {
            const row = el.closest('tr');
            return [el.innerText.trim(), row ? row.innerText.split(/\\s+/) : []];
        })""",
    )
    statuses = {}
    for status, words in pairs:
        for name in set(words) & names:
            statuses[name] = status
    return statuses


def monitor_sessions(page, home_url, session_names, duration_s, interval_s, label):
    """Poll the session list on `page` every interval_s for duration_s and
    return the drops: [{session, status, seconds_after_start, time}], one per
    session that left the list or stopped being Active/Executing.

    Writes evidence/session_monitor_<label>.csv (every poll: time and each
    session's status) and, when there are drops, session_drops_<label>.json
    plus a screenshot of the session list when each drop was seen; all are
    attached to Allure. A poll that cannot read the list is logged and
    skipped (it does not count as a drop)."""
    session_names = list(session_names)
    csv_path = os.path.join(EVIDENCE_DIR, "session_monitor_%s.csv" % label)
    started = time.time()
    drops = {}
    log_step("Monitoring %d session(s) for %.0fs, checking every %.0fs", len(session_names), duration_s, interval_s)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time", "Seconds since tabs closed"] + session_names)
        while True:
            elapsed = time.time() - started
            try:
                goto_session_list(page, home_url, settle_ms=1000)
                load_all_session_rows(page)
                statuses = session_statuses_by_name(page, session_names)
            except Exception as exc:
                log_step("Session monitor: could not read the session list at %.0fs: %s", elapsed, exc)
                statuses = None
            if statuses is not None:
                writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), round(elapsed)]
                                + [statuses.get(n, GONE) for n in session_names])
                f.flush()
                new = [n for n in session_names if n not in drops and statuses.get(n) not in ACTIVE_STATUSES]
                for name in new:
                    drops[name] = {"session": name, "status": statuses.get(name, GONE),
                                   "seconds_after_start": round(elapsed),
                                   "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                    log_step("SESSION DROP: %s is %s, %.0fs after its tab was closed",
                             name, drops[name]["status"], elapsed)
                if new:
                    save_screenshot(page, "session_drop_%s_%ds.png" % (label, elapsed), always=True)
                else:
                    log_step("Session monitor at %.0fs of %.0fs: %d of %d session(s) still Active/Executing",
                             elapsed, duration_s, len(session_names) - len(drops), len(session_names))
            if elapsed >= duration_s:
                break
            page.wait_for_timeout(max(0, min(interval_s, duration_s - (time.time() - started))) * 1000)
    attach_allure_file(csv_path)
    if drops:
        json_path = os.path.join(EVIDENCE_DIR, "session_drops_%s.json" % label)
        write_json_report(json_path, list(drops.values()))
        attach_allure_file(json_path)
        log_step("%d session(s) dropped while their tabs were closed: %s",
                 len(drops), ", ".join("%s (%s)" % (d["session"], d["status"]) for d in drops.values()))
    else:
        log_step("No session dropped: all %d stayed Active/Executing for %.0fs", len(session_names), duration_s)
    return list(drops.values())


def stop_console_scripts(context, home_url, session_names):
    """Reopen each of `session_names` in a new tab, interrupt the R script
    running in its console (Escape) and check R answers again, so the
    script is stopped before the session is quit. Returns {name: error} for
    the sessions that could not be stopped."""
    failures = {}
    for name in session_names:
        tab = context.new_page()
        try:
            open_existing_session(tab, home_url, name)
            tab.locator(locators.CONSOLE_INPUT_SELECTOR).first.click(force=True, timeout=CONSOLE_WAIT_TIMEOUT_MS)
            tab.keyboard.press("Escape")
            # R only answers once the script has stopped
            run_console_command(tab, "invisible(NULL)", timeout_ms=30000)
            log_step("Stopped the R script in session %s", name)
        except Exception as exc:
            failures[name] = " ".join(str(exc).split())[:200]
            log_step("Could not stop the R script in session %s: %s", name, failures[name])
            save_screenshot(tab, "stop_script_failed_%s.png" % name, always=True)
        finally:
            tab.close()
    return failures
