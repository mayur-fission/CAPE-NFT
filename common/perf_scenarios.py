"""End-to-end launch-performance scenarios: log in, launch session(s), write
JSON/CSV/Allure reports, then clean up everything the run created.
"""
import os

from common.rstudio_session_helper import row_ids as _row_ids

from config.perf_report import attach_allure_reports as _attach_allure_reports
from config.perf_report import build_csv_rows as _build_csv_rows
from config.perf_report import build_stats as _build_stats
from config.perf_report import write_csv_report as _write_csv_report
from config.perf_report import write_json_report as _write_json_report

from common.session_actions import login_to_posit_workbench
from common.session_batch import create_multiple_sessions
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_concurrent import concurrent_launch_sessions
from common.session_retry import _LAUNCH_ATTEMPTS
from common.otel_metrics import capture_otel_metrics

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV_PATH = os.path.join(_REPO_ROOT, "evidence", "rstudio_session_aggregate_report.csv")
DEFAULT_JSON_PATH = os.path.join(_REPO_ROOT, "evidence", "rstudio_session_launch_perf.json")
DEFAULT_SINGLE_CSV_PATH = os.path.join(_REPO_ROOT, "evidence", "rstudio_single_session_aggregate_report.csv")
DEFAULT_SINGLE_JSON_PATH = os.path.join(_REPO_ROOT, "evidence", "rstudio_single_session_launch_perf.json")
DEFAULT_CONCURRENT_CSV_PATH = os.path.join(
    _REPO_ROOT, "evidence", "rstudio_concurrent_sessions_aggregate_report.csv"
)
DEFAULT_CONCURRENT_JSON_PATH = os.path.join(
    _REPO_ROOT, "evidence", "rstudio_concurrent_sessions_launch_perf.json"
)


def generate_csv_report(results, run_elapsed, path=None):
    """Write the JMeter Aggregate Report style CSV for `results` to `path`
    (default DEFAULT_CSV_PATH) and return the path.
    """
    path = path or DEFAULT_CSV_PATH
    rows = _build_csv_rows(results, run_elapsed)
    _write_csv_report(path, rows)
    return path


def generate_allure_report(stats, csv_path):
    """Attach the stats and CSV to the Allure report (no-op without allure)."""
    _attach_allure_reports(stats, csv_path)


def _assert_created_rows_match(created_ids, ok_results, results, kind="sessions"):
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
        "launched %d %s but found %d new row(s) on the session list - check "
        "for concurrent activity on this shared instance"
        % (len(ok_results), kind, len(created_ids))
    )


def run_launch_perf_single(page, p95_max_seconds=None, json_path=None, csv_path=None):
    """run_launch_perf_multiple() for one session, writing to its own
    default report files. Returns (stats, csv_path).
    """
    return run_launch_perf_multiple(
        page,
        count=1,
        p95_max_seconds=p95_max_seconds,
        json_path=json_path or DEFAULT_SINGLE_JSON_PATH,
        csv_path=csv_path or DEFAULT_SINGLE_CSV_PATH,
        scenario="single",
    )


def run_launch_perf_multiple(page, count=None, p95_max_seconds=None,
                                              json_path=None, csv_path=None, scenario="multiple"):
    """Log in, launch `count` sessions (default RSTUDIO_SESSION_COUNT), write
    the reports, then clean up the run's sessions, pass or fail.

    Raises AssertionError if a launch failed, the new rows don't match the
    launches, cleanup left a session behind, or p95 exceeds p95_max_seconds.
    Returns (stats, csv_path).
    """
    home_url = login_to_posit_workbench(page)
    before_ids = _row_ids(page)

    results, created_ids, run_elapsed = create_multiple_sessions(page, home_url, count=count)
    ok_results = [r for r in results if r["ok"]]
    failures = [(r["index"], r["error"]) for r in results if not r["ok"]]

    try:
        assert not failures, (
            "sessions that failed to launch after %d attempt(s) each: %s"
            % (_LAUNCH_ATTEMPTS, failures)
        )
        _assert_created_rows_match(created_ids, ok_results, results, kind="sessions")

        stats = _build_stats(ok_results, run_elapsed)
        print("\n[rstudio-local] launch performance over %d session(s): %s" % (stats["count"], stats))
        _write_json_report(json_path or DEFAULT_JSON_PATH, stats)

        written_csv_path = generate_csv_report(results, run_elapsed, path=csv_path)
        print("[rstudio-local] wrote JMeter-style aggregate report to %s" % written_csv_path)

        generate_allure_report(stats, written_csv_path)
        capture_otel_metrics(results, run_elapsed, scenario=scenario)

        if p95_max_seconds:
            assert stats["p95_s"] <= p95_max_seconds, (
                "p95 launch time %.2fs exceeds the %.2fs limit" % (stats["p95_s"], p95_max_seconds)
            )

        return stats, written_csv_path
    finally:
        # Clean up whatever this run created, pass or fail - a perf test
        # that leaves N sessions behind on every run isn't reusable.
        cleanup_and_verify_sessions(page, home_url, before_ids)


def run_launch_perf_concurrent(page, count=None, max_workers=None, p95_max_seconds=None,
                                                json_path=None, csv_path=None):
    """Same as run_launch_perf_multiple(), but launches sessions in parallel,
    each in its own isolated browser. `page` is only used for login, the
    row diff and cleanup. Writes to its own default report files.
    """
    home_url = login_to_posit_workbench(page)
    before_ids = _row_ids(page)

    results, run_elapsed = concurrent_launch_sessions(page, count=count, max_workers=max_workers)
    ok_results = [r for r in results if r["ok"]]
    failures = [(r["index"], r["error"]) for r in results if not r["ok"]]

    # Sessions are server-side state, not tied to any one browser tab, so a
    # single diff on `page` sees everything every worker's own browser created.
    page.goto(home_url)
    page.get_by_text("New Session", exact=True).first.wait_for(state="visible", timeout=30000)
    page.wait_for_timeout(2000)
    created_ids = _row_ids(page) - before_ids

    try:
        assert not failures, (
            "sessions that failed to launch after %d attempt(s) each: %s"
            % (_LAUNCH_ATTEMPTS, failures)
        )
        _assert_created_rows_match(
            created_ids, ok_results, results, kind="concurrent sessions"
        )

        stats = _build_stats(ok_results, run_elapsed)
        print("\n[rstudio-local] concurrent launch performance over %d session(s): %s" % (stats["count"], stats))
        _write_json_report(json_path or DEFAULT_CONCURRENT_JSON_PATH, stats)

        written_csv_path = generate_csv_report(results, run_elapsed, path=csv_path or DEFAULT_CONCURRENT_CSV_PATH)
        print("[rstudio-local] wrote JMeter-style aggregate report to %s" % written_csv_path)

        generate_allure_report(stats, written_csv_path)
        capture_otel_metrics(results, run_elapsed, scenario="concurrent")

        if p95_max_seconds:
            assert stats["p95_s"] <= p95_max_seconds, (
                "p95 launch time %.2fs exceeds the %.2fs limit" % (stats["p95_s"], p95_max_seconds)
            )

        return stats, written_csv_path
    finally:
        # Clean up whatever this run created, pass or fail.
        cleanup_and_verify_sessions(page, home_url, before_ids)
