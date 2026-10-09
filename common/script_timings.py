"""Per-run R script timings: run console commands while recording each one's
start/end time and status, wait for several runs at once, and write the
records to a CSV under evidence/.

A run record is a dict with "session_name", "started", "ended" (epoch
seconds; "ended" stays None unless the run finished) and "status" ("ok",
"timed out" or an error message).
"""
import os
import time
from datetime import datetime

from config.perf_report import write_csv_report

from common.config import EVIDENCE_DIR
from common.evidence import save_screenshot
from common.rstudio_console_commands import (
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
        record["status"] = "timed out" if timed_out else str(exc)
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


def submit_in_each(ready, command):
    """Submit `command` without waiting in each (page, record, context) of
    `ready`, setting record["started"], and screenshot each console as
    console_input_<session name>.png. Returns the (page, marker, record,
    context) tuples for wait_for_console_runs(); a submit that fails is
    recorded in its record instead.
    """
    pending = []
    for page, record, context in ready:
        try:
            marker, record["started"] = submit_console_command(page, command)
            pending.append((page, marker, record, context))
        except Exception as exc:
            record["status"] = "submit failed: %s" % exc
            save_screenshot(page, "console_failed_%s.png" % record["session_name"], always=True)
    for page, _, record, _ in pending:
        save_screenshot(page, "console_input_%s.png" % record["session_name"])
    return pending


def wait_for_console_runs(pending, timeout_ms, poll_interval_ms=500, on_done=None, on_timeout=None,
                          on_poll=None):
    """Poll submitted console commands until each has finished or run for
    `timeout_ms`, so commands in several tabs run concurrently.

    `pending` holds (page, marker, record, context) tuples, where marker
    comes from submit_console_command() and record["started"] is set. Each
    record ends as "ok" (with "ended"), "timed out", or the error raised
    while checking it. on_done/on_timeout(page, record, context) run when a
    command finishes / times out (on_done may set an error status, e.g. for
    a missing output file); on_poll() runs once per round - keep it quick.
    Each console is screenshotted when its command finishes
    (console_output_<session name>.png, before on_done) or times out
    (console_timeout_<session name>.png).
    """
    pending = list(pending)
    while pending:
        for item in list(pending):
            page, marker, record, context = item
            try:
                done = is_console_command_done(page, marker)
            except Exception as exc:
                record["status"] = str(exc)
                pending.remove(item)
                print("\n[rstudio-local] session %s: could not check the console: %s" % (record["session_name"], exc))
                continue
            if done:
                record["ended"], record["status"] = time.time(), "ok"
                pending.remove(item)
                save_screenshot(page, "console_output_%s.png" % record["session_name"])
                if on_done:
                    on_done(page, record, context)
            elif time.time() - record["started"] >= timeout_ms / 1000.0:
                record["status"] = "timed out"
                pending.remove(item)
                save_screenshot(page, "console_timeout_%s.png" % record["session_name"], always=True)
                if on_timeout:
                    on_timeout(page, record, context)
        if on_poll:
            on_poll()
        if pending:
            pending[0][0].wait_for_timeout(poll_interval_ms)


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
