"""Per-run R script timings: run a console command while recording its
start/end time and status, and write the records to a CSV under evidence/.

A record is a dict with "session_name", "started", "ended" (epoch seconds,
"ended" None unless it finished) and "status" ("ok", "timed out" or the
error message).
"""
import os
import time
from datetime import datetime

from config.perf_report import write_csv_report

from common.session_actions import submit_console_command, wait_for_console_command

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def script_timings_csv_path(name):
    """evidence/rstudio_script_timings_<name>.csv"""
    return os.path.join(_REPO_ROOT, "evidence", "rstudio_script_timings_%s.csv" % name)


def run_timed_console_command(page, session_name, command, timeout_ms, script_runs=None):
    """run_console_command() that also appends a record to `script_runs`
    (when given). Returns the elapsed seconds; re-raises on failure after
    recording it.
    """
    record = {"session_name": session_name, "started": None, "ended": None, "status": "not run"}
    if script_runs is not None:
        script_runs.append(record)
    try:
        marker, record["started"] = submit_console_command(page, command)
        elapsed_s = wait_for_console_command(page, marker, record["started"], timeout_ms=timeout_ms)
    except Exception as exc:
        timed_out = (
            record["started"] is not None
            and time.time() - record["started"] >= timeout_ms / 1000.0
        )
        record["status"] = "timed out" if timed_out else str(exc)
        raise
    record["ended"] = record["started"] + elapsed_s
    record["status"] = "ok"
    return elapsed_s


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
