"""Report-writing helpers for RStudio session launch performance runs.

Kept separate from tests/test_rstudio_session_perf.py so that file only
orchestrates the browser flow (login, launch, quit); everything about
*recording* what happened - the JSON summary, the JMeter-shaped CSV, the
Allure attachments and steps - lives here as one function per concern.
"""
import csv
import json
import os
import statistics
from contextlib import nullcontext

try:
    import allure
    HAS_ALLURE = True
except ImportError:
    HAS_ALLURE = False

CSV_HEADER = [
    "Label", "# Samples", "Average", "Median", "90% Line", "95% Line",
    "99% Line", "Min", "Max", "Error %", "Throughput", "Received KB/sec",
    "Std. Dev.",
]


def percentile(sorted_values, pct):
    """Nearest-rank percentile - fine for the sample sizes these tests run."""
    rank = max(1, int(round(pct / 100.0 * len(sorted_values))))
    return sorted_values[rank - 1]


def build_stats(ok_results, run_elapsed):
    """Summary dict for the JSON report, from the successful launch results
    (dicts with at least an "elapsed_s" key).
    """
    timings = sorted(r["elapsed_s"] for r in ok_results)
    return {
        "count": len(timings),
        "min_s": round(timings[0], 3),
        "max_s": round(timings[-1], 3),
        "mean_s": round(sum(timings) / len(timings), 3),
        "median_s": round(percentile(timings, 50), 3),
        "p95_s": round(percentile(timings, 95), 3),
        "total_wall_s": round(run_elapsed, 3),
        "throughput_sessions_per_min": round(len(timings) / run_elapsed * 60, 3) if run_elapsed else None,
    }


def _fmt(value):
    return round(value, 3) if value is not None else ""


def _aggregate_row(label, timings_s, byte_counts, wall_s, error_pct):
    """One JMeter Aggregate Report row. timings_s/byte_counts are the
    successful samples this row covers; wall_s is the elapsed real time to
    use for throughput (a single sample's own time, or the whole run's).
    """
    if not timings_s:
        return [label, len(timings_s), "", "", "", "", "", "", "", round(error_pct, 2), "", "", ""]

    sorted_t = sorted(timings_s)
    throughput = round(len(timings_s) / wall_s, 3) if wall_s else ""
    kb_per_sec = round((sum(byte_counts) / 1024.0) / wall_s, 3) if wall_s else ""
    std_dev = round(statistics.pstdev(timings_s), 3) if len(timings_s) > 1 else 0.0

    return [
        label,
        len(timings_s),
        _fmt(sum(sorted_t) / len(sorted_t)),
        _fmt(percentile(sorted_t, 50)),
        _fmt(percentile(sorted_t, 90)),
        _fmt(percentile(sorted_t, 95)),
        _fmt(percentile(sorted_t, 99)),
        _fmt(sorted_t[0]),
        _fmt(sorted_t[-1]),
        round(error_pct, 2),
        throughput,
        kb_per_sec,
        std_dev,
    ]


def build_csv_rows(results, run_elapsed):
    """JMeter Aggregate Report shaped rows (including the header row): one
    row per launch attempt - Label names that session, a failed attempt gets
    Error % = 100 with blank timing fields - plus a JMeter-style TOTAL row
    aggregating every successful launch in the run.

    results: list of dicts, one per attempt, each either
        {"index": N, "ok": True, "elapsed_s": ..., "bytes_received": ...}
        {"index": N, "ok": False, "error": ...}
    """
    ok_results = [r for r in results if r["ok"]]
    failures = [r for r in results if not r["ok"]]

    rows = [CSV_HEADER]
    for r in results:
        label = "Launch RStudio Pro Session %d" % r["index"]
        if r["ok"]:
            rows.append(_aggregate_row(
                label, [r["elapsed_s"]], [r["bytes_received"]], r["elapsed_s"], error_pct=0.0
            ))
        else:
            rows.append(_aggregate_row(label, [], [], None, error_pct=100.0))

    total_error_pct = (len(failures) / len(results)) * 100 if results else 0.0
    rows.append(_aggregate_row(
        "TOTAL",
        [r["elapsed_s"] for r in ok_results],
        [r["bytes_received"] for r in ok_results],
        run_elapsed,
        error_pct=total_error_pct,
    ))
    return rows


def write_json_report(path, stats):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(stats, fh, indent=2)


def write_csv_report(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)


def attach_allure_reports(stats, csv_path):
    """Attaches the JSON stats and the CSV to the Allure report. No-ops if
    allure-pytest isn't installed.
    """
    if not HAS_ALLURE:
        return
    allure.attach(
        json.dumps(stats, indent=2), name="launch_perf_stats", attachment_type=allure.attachment_type.JSON
    )
    with open(csv_path) as fh:
        allure.attach(fh.read(), name="aggregate_report", attachment_type=allure.attachment_type.CSV)


def allure_step(name):
    """Context manager for a named Allure step, or a no-op if allure-pytest
    isn't installed.
    """
    return allure.step(name) if HAS_ALLURE else nullcontext()
