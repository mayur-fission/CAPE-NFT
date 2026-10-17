"""Per-run R script timings: run console commands recording start/end
and status, wait for several runs at once, write the CSV.

A run record: {"session_name", "started", "ended" (None unless finished),
"status" ("ok", "timed out" or an error)}.
"""
import os
import time
from datetime import datetime

from common.perf_report import write_csv_report

from common import locators
from config.config import EVIDENCE_DIR, env
from common.evidence import save_screenshot
from common.step_log import log_step
from common.rstudio_console_commands import (
    RCommandError,
    is_console_command_done,
    submit_console_command,
    wait_for_console_command,
)


def script_timings_csv_path(name):
    """evidence/rstudio_script_timings_<name>.csv"""
    return os.path.join(EVIDENCE_DIR, "rstudio_script_timings_%s.csv" % name)


def new_run_record(session_name):
    """A run record for `session_name` that has not started yet."""
    return {"session_name": session_name, "started": None, "ended": None, "status": "not run"}


def failed_runs(runs):
    """"<session>: <status>" for every run that is not "ok"."""
    return ["%s: %s" % (r["session_name"], r["status"]) for r in runs if r["status"] != "ok"]


def run_timed_console_command(page, session_name, command, timeout_ms, script_runs=None):
    """Run `command` in the console and wait for it, appending its record to
    `script_runs` (when given). Returns the elapsed seconds; re-raises on
    failure after recording it.
    """
    record = new_run_record(session_name)
    if script_runs is not None:
        script_runs.append(record)
    try:
        marker, record["started"] = submit_console_command(page, command)
        save_screenshot(page, "console_input_%s.png" % session_name)
        elapsed_s = wait_for_console_command(page, marker, record["started"], timeout_ms=timeout_ms)
    except Exception as exc:
        timed_out = (
            record["started"] is not None
            and time.time() - record["started"] >= timeout_ms / 1000.0
        )
        record["status"] = "timed out" if timed_out else (
            "R error: %s" % exc if isinstance(exc, RCommandError) else str(exc))
        save_screenshot(page, "console_%s_%s.png" % ("timeout" if timed_out else "failed", session_name),
                        always=True)
        raise
    record["ended"] = record["started"] + elapsed_s
    record["status"] = "ok"
    save_screenshot(page, "console_output_%s.png" % session_name)
    return elapsed_s


def run_timed_console_command_repeatedly(page, session_name, command, timeout_ms, run_count=1,
                                         think_time_s=0.0, script_runs=None):
    """run_timed_console_command() `run_count` times back to back, sleeping
    think_time_s between runs (never after the last). Returns the elapsed
    seconds of each run.
    """
    elapsed = []
    for i in range(run_count):
        elapsed.append(run_timed_console_command(page, session_name, command, timeout_ms, script_runs))
        if think_time_s and i + 1 < run_count:
            time.sleep(think_time_s)
    return elapsed


def submit_in_each(ready, command, what=None):
    """Submit `command` without waiting in each (page, record, context) of
    `ready`, setting record["started"], and screenshot each console as
    console_input_<session name>.png. Returns the (page, marker, record,
    context) tuples for wait_for_console_runs(); a submit that fails is
    recorded in its record instead. With `what` (e.g. "R script x.R"),
    each start or failed start is logged with log_step().
    """
    pending = []
    for page, record, context in ready:
        try:
            marker, record["started"] = submit_console_command(page, command)
            pending.append((page, marker, record, context))
            if what:
                log_step("%s started execution in session %s", what, record["session_name"])
        except Exception as exc:
            record["status"] = "submit failed: %s" % exc
            if what:
                log_step("%s could not be started in session %s: %s", what, record["session_name"], exc)
            save_screenshot(page, "console_failed_%s.png" % record["session_name"], always=True)
    for page, _, record, _ in pending:
        save_screenshot(page, "console_input_%s.png" % record["session_name"])
    return pending


# How many times a run's tab may crash and be reopened before the run fails.
TAB_CRASH_REOPENS = int(env("CONSOLE_TAB_CRASH_REOPENS", "3"))


def _is_tab_crash(exc):
    return "Target crashed" in str(exc)


def _reopen_crashed_tab(page, timeout_ms=180000):
    """Open the session of a crashed tab again in a new tab of the same
    browser and wait for its console, without sending anything to R (it
    may still be busy with the command). RStudio replays the console on
    reconnect, so the command's marker is found once it has printed.
    Returns the new page."""
    new_page = page.context.new_page()
    try:
        new_page.goto(page.url, wait_until="domcontentloaded", timeout=timeout_ms)
        new_page.locator(locators.CONSOLE_OUTPUT_SELECTOR).wait_for(state="attached", timeout=timeout_ms)
    except Exception:
        new_page.close()
        raise
    try:
        page.close()
    except Exception:
        pass
    return new_page


def wait_for_console_runs(pending, timeout_ms, poll_interval_ms=500, on_done=None, on_timeout=None,
                          on_poll=None, what=None):
    """Poll submitted commands (page, marker, record, context) until each
    finishes, errors or reaches `timeout_ms`, so several tabs run at once.

    Flow: poll every console -> record end time / "R error: ..." / "timed out"
          -> (all done) screenshots + on_done / on_timeout per tab

    The loop only records end times; screenshots and on_done (seconds each,
    e.g. output checks) run afterwards, so they cannot delay - and inflate -
    the next tab's end time. A crashed tab is reopened (CONSOLE_TAB_CRASH_REOPENS)
    and checked again. on_poll() runs once per round.
    """
    what_ = what or "The command"
    pending = list(pending)
    finished = []  # (page, record, context) of finished commands, handled after the loop
    timed_out = []
    while pending:
        for item in list(pending):
            page, marker, record, context = item
            name = record["session_name"]
            try:
                done = is_console_command_done(page, marker)
            except RCommandError as exc:
                record["status"] = "R error: %s" % exc
                pending.remove(item)
                log_step("%s stopped with an R error in session %s after %.2fs: %s",
                         what_, name, time.time() - record["started"], exc)
                save_screenshot(page, "console_failed_%s.png" % name, always=True)
                continue
            except Exception as exc:
                if _is_tab_crash(exc) and record.get("reopens", 0) < TAB_CRASH_REOPENS:
                    record["reopens"] = record.get("reopens", 0) + 1
                    log_step("Tab of session %s crashed - reopening it (%d of %d)",
                             name, record["reopens"], TAB_CRASH_REOPENS)
                    try:
                        page = _reopen_crashed_tab(page)
                        pending[pending.index(item)] = (page, marker, record, context)
                        log_step("Reopened the tab of session %s", name)
                        continue
                    except Exception as reopen_exc:
                        exc = "tab crashed and could not be reopened: %s" % reopen_exc
                record["status"] = str(exc)
                pending.remove(item)
                log_step("Could not check the console of session %s: %s", name, exc)
                continue
            if done:
                record["ended"], record["status"] = time.time(), "ok"
                pending.remove(item)
                if what:
                    log_step("%s finished execution in session %s in %.2fs", what, name,
                             record["ended"] - record["started"])
                finished.append((page, record, context))
            elif time.time() - record["started"] >= timeout_ms / 1000.0:
                record["status"] = "timed out"
                log_step("%s timed out in session %s after %.0fs", what_, name, timeout_ms / 1000.0)
                pending.remove(item)
                timed_out.append((page, record, context))
        if on_poll:
            on_poll()
        if pending:
            pending[0][0].wait_for_timeout(poll_interval_ms)

    for page, record, context in finished:
        save_screenshot(page, "console_output_%s.png" % record["session_name"])
        if on_done:
            on_done(page, record, context)
    for page, record, context in timed_out:
        save_screenshot(page, "console_timeout_%s.png" % record["session_name"], always=True)
        if on_timeout:
            on_timeout(page, record, context)


def _format_timestamp(epoch_s):
    """Local "YYYY-MM-DD HH:MM:SS.mmm" for an epoch time, "" for None."""
    if epoch_s is None:
        return ""
    return datetime.fromtimestamp(epoch_s).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def write_script_timings_csv(path, script_runs):
    """One row per record, ordered by start time: session name, start/end
    time, time taken (s) and status. End time and time taken are blank for
    runs that errored or timed out. Does nothing if there are no records.
    """
    if not script_runs:
        return
    rows = [["Session Name", "Start Time", "End Time", "Time Taken (s)", "Status"]]
    for run in sorted(script_runs, key=lambda r: r["started"] or 0):
        ended = run.get("ended")
        rows.append([
            run["session_name"],
            _format_timestamp(run["started"]),
            _format_timestamp(ended),
            "%.2f" % (ended - run["started"]) if ended is not None else "",
            run.get("status") or "not run",
        ])
    write_csv_report(path, rows)
    print("[rstudio-local] wrote script timings to %s" % path)
