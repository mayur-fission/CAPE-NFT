"""Helpers for tests/test_concurrent_sessions.py: launch N sessions named
CONCURRENT_SESSION_1 .. CONCURRENT_SESSION_<N> from the UI, spread over
several browsers with a handful of tabs each, source one R script in all of
them at the same moment and time every run.

Each browser runs in its own thread (run_in_parallel_browsers()), logs in
as the same user and only touches its own sessions. Browsers wait for each
other before the scripts start, so the runs overlap, and before cleanup.

Run records use the common.script_timings format.
"""
import math
import os
import threading
import time

from config.perf_report import attach_allure_file, write_json_report

from common.api_ui_combo import (
    browser_name_prefixes,
    launch_sessions_in_tabs,
    login_and_quit_sessions_named,
    quit_ui_sessions_named,
    report_failed_runs,
    run_in_parallel_browsers,
)
from common.config import EVIDENCE_DIR, env
from common.launch_already_created_sessions import close_launched_sessions, run_script_in_launched_sessions
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
SESSION_HOME_DIR = env("CONCURRENT_SESSIONS_HOME_DIR", "/home/posit")


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
                                      retry_wait_s=15, delete_output=False):
    """Spread `session_names` over `browser_count` browsers (see
    split_across_browsers()). Each browser logs in as `user`, quits leftover
    sessions with its names, launches each name in its own tab, waits for
    the other browsers, then sources `script_path` in all its tabs at once
    (each in `output_dir`/<session name>, emptied first; a run only counts
    as ok if `output_file_name` is there afterwards) and waits up to
    `script_timeout_ms`. Browsers start `stagger_s` apart. With
    delete_output, each session's output (file or folder) is deleted once
    it has been checked, so large outputs do not pile up on the server.

    The scripts are skipped everywhere if any browser failed to launch its
    sessions. Tabs are closed and the created sessions quit even on failure.

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
    done = threading.Barrier(browser_count)
    launch_failed = set()

    def _worker(context, prefix):
        return _run_script_in_browser(
            context, prefix, names_by_browser[prefix], user, "%s %s" % (label, prefix.rstrip("_")),
            ready, done, launch_failed, attempts, launch_timeout_ms, retry_wait_s,
            script_path, output_dir, output_file_name, script_timeout_ms, delete_output,
        )

    results = run_in_parallel_browsers(
        browser_name, launch_args, context_args, prefixes, _worker, barriers=(ready, done), stagger_s=stagger_s,
    )
    runs, errors = [], []
    for prefix, result, error in results:
        if result is not None:
            browser_runs, error = result
            runs += browser_runs
        if error is not None:
            errors.append("browser %s: %s" % (prefix.rstrip("_"), error))
    return runs, errors


def _run_script_in_browser(context, prefix, session_names, user, label, ready, done, launch_failed,
                           attempts, launch_timeout_ms, retry_wait_s, script_path, output_dir,
                           output_file_name, script_timeout_ms, delete_output=False):
    """One browser's part of run_script_in_concurrent_sessions(); a browser
    whose launch fails adds its prefix to `launch_failed`. Returns (runs,
    error): its run records and the error that failed it, else None."""
    where = " in browser %s" % prefix.rstrip("_")
    home_url = before_ids = None
    launched, runs, error = [], [], None
    try:
        try:
            home_url, before_ids = login_and_quit_sessions_named([context], user, session_names)
            launched, failed = launch_sessions_in_tabs(
                context, home_url, session_names, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
                retry_wait_s=retry_wait_s, where=where,
            )
            assert not failed, "sessions that failed to launch: %s" % failed
            for session in launched:
                set_session_working_directory(session.page, path=SESSION_HOME_DIR)
                log_step("Working directory set to %s in session %s", SESSION_HOME_DIR, session.session_name)
        except Exception:
            launch_failed.add(prefix)
            raise
        finally:
            log_step("%d of %d session(s) ready in this browser - waiting for the other browsers",
                     len(launched), len(session_names))
            ready.wait()
        if launch_failed:
            raise AssertionError("scripts skipped: launch failed in browser(s) %s"
                                 % sorted(p.rstrip("_") for p in launch_failed))
        log_step("All browsers are ready - starting the R script %s in every session",
                 os.path.basename(script_path))
        started = time.time()
        runs += run_script_in_launched_sessions(
            launched, script_path, working_dir=output_dir, timeout_ms=script_timeout_ms,
            output_path=output_file_name, per_session_dir=True, delete_output=delete_output,
        )
        failures = report_failed_runs(runs, label, started)
        assert not failures, "runs that did not finish: %s" % failures
    except Exception as exc:
        error = exc
    finally:
        try:
            done.wait()
        except threading.BrokenBarrierError:
            pass
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
