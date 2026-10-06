"""End-to-end launch-performance scenarios: log in, launch session(s), write
JSON / JMeter-style CSV / Allure reports and OTel metrics, then quit every
session the run created, pass or fail.
"""
import os

from config.perf_report import (
    attach_allure_reports,
    build_csv_rows,
    build_stats,
    write_csv_report,
    write_json_report,
)

from common.config import EVIDENCE_DIR
from common.otel_metrics import capture_otel_metrics
from common.rstudio_session_helper import goto_session_list, row_ids
from common.session_actions import login_to_posit_workbench
from common.session_batch import create_multiple_sessions
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_concurrent import concurrent_launch_sessions
from common.session_retry import LAUNCH_ATTEMPTS


def _evidence(file_name):
    return os.path.join(EVIDENCE_DIR, file_name)


DEFAULT_CSV_PATH = _evidence("rstudio_session_aggregate_report.csv")
DEFAULT_JSON_PATH = _evidence("rstudio_session_launch_perf.json")
DEFAULT_SINGLE_CSV_PATH = _evidence("rstudio_single_session_aggregate_report.csv")
DEFAULT_SINGLE_JSON_PATH = _evidence("rstudio_single_session_launch_perf.json")
DEFAULT_CONCURRENT_CSV_PATH = _evidence("rstudio_concurrent_sessions_aggregate_report.csv")
DEFAULT_CONCURRENT_JSON_PATH = _evidence("rstudio_concurrent_sessions_launch_perf.json")


def _assert_created_rows_match(created_ids, ok_results, results):
    """Check that the new session rows match the successful launches.

    Extra rows are allowed (and reported) when retries happened, since a
    failed attempt can still leave a session behind. Fewer rows is an error.
    """
    if len(created_ids) == len(ok_results):
        return

    retries = sum(r.get("attempts", 1) - 1 for r in results)
    if len(created_ids) > len(ok_results) and retries:
        print(
            "\n[rstudio-local] note: %d extra session row(s) beyond the %d "
            "successful launch(es), from %d retried attempt(s) that reached "
            "the server before failing - cleaning them up with the rest"
            % (len(created_ids) - len(ok_results), len(ok_results), retries)
        )
        return

    raise AssertionError(
        "launched %d session(s) but found %d new row(s) on the session list - check "
        "for concurrent activity on this shared instance" % (len(ok_results), len(created_ids))
    )


def _check_and_report(results, created_ids, run_elapsed, scenario, json_path, csv_path, p95_max_seconds):
    """Assert every launch succeeded and matches the new rows, write the JSON,
    CSV and Allure reports and the OTel metrics, then apply the p95 limit.
    Returns (stats, csv_path)."""
    ok_results = [r for r in results if r["ok"]]
    failures = [(r["index"], r["error"]) for r in results if not r["ok"]]
    assert not failures, (
        "sessions that failed to launch after %d attempt(s) each: %s" % (LAUNCH_ATTEMPTS, failures)
    )
    _assert_created_rows_match(created_ids, ok_results, results)

    stats = build_stats(ok_results, run_elapsed)
    print("\n[rstudio-local] %s launch performance over %d session(s): %s" % (scenario, stats["count"], stats))
    write_json_report(json_path, stats)
    write_csv_report(csv_path, build_csv_rows(results, run_elapsed))
    print("[rstudio-local] wrote JMeter-style aggregate report to %s" % csv_path)
    attach_allure_reports(stats, csv_path)
    capture_otel_metrics(results, run_elapsed, scenario=scenario)

    if p95_max_seconds:
        assert stats["p95_s"] <= p95_max_seconds, (
            "p95 launch time %.2fs exceeds the %.2fs limit" % (stats["p95_s"], p95_max_seconds)
        )
    return stats, csv_path


def run_launch_perf_multiple(page, count=None, p95_max_seconds=None, json_path=None, csv_path=None,
                             scenario="multiple"):
    """Log in, launch `count` sessions (default RSTUDIO_SESSION_COUNT) one
    after another, write the reports, then quit the run's sessions.

    Raises AssertionError if a launch failed, the new rows don't match the
    launches, cleanup left a session behind, or p95 exceeds p95_max_seconds.
    Returns (stats, csv_path).
    """
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)
    try:
        results, created_ids, run_elapsed = create_multiple_sessions(page, home_url, count=count)
        return _check_and_report(
            results, created_ids, run_elapsed, scenario,
            json_path or DEFAULT_JSON_PATH, csv_path or DEFAULT_CSV_PATH, p95_max_seconds,
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)


def run_launch_perf_single(page, p95_max_seconds=None, json_path=None, csv_path=None):
    """run_launch_perf_multiple() for one session, with its own report files."""
    return run_launch_perf_multiple(
        page, count=1, p95_max_seconds=p95_max_seconds,
        json_path=json_path or DEFAULT_SINGLE_JSON_PATH, csv_path=csv_path or DEFAULT_SINGLE_CSV_PATH,
        scenario="single",
    )


def run_launch_perf_concurrent(page, count=None, max_workers=None, p95_max_seconds=None,
                               json_path=None, csv_path=None):
    """run_launch_perf_multiple(), but the sessions launch in parallel, each
    in its own isolated browser; `page` is only used for login, the row diff
    and cleanup. Writes its own report files.
    """
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)
    try:
        results, run_elapsed = concurrent_launch_sessions(page, count=count, max_workers=max_workers)
        # Sessions are server-side state, so one diff on `page` sees what
        # every worker's own browser created.
        goto_session_list(page, home_url, settle_ms=2000)
        return _check_and_report(
            results, row_ids(page) - before_ids, run_elapsed, "concurrent",
            json_path or DEFAULT_CONCURRENT_JSON_PATH, csv_path or DEFAULT_CONCURRENT_CSV_PATH, p95_max_seconds,
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)
