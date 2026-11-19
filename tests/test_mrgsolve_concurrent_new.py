"""Proof that N sessions (e.g. 200, from several workspaces) run the
mrgsolve script run_mrgsolve.R at the same time.

Like test_mrgsolve_concurrent (same MRGSOLVE_* settings, session names and
run folders), with these differences:

1. Server-side start. Every session sources a small wrapper
   (common/mrgsolve_concurrency.py) instead of MRGSOLVE_SCRIPT. Three modes:
   - spike (MRGSOLVE_START_AT set, the same in every workspace): the
     wrapper waits on the server clock until then, so the sessions of all
     workspaces start within ~0.2s of each other - the proof of "N at once";
   - staggered (MRGSOLVE_START_AT plus MRGSOLVE_RAMP_UP_S or
     MRGSOLVE_START_INTERVAL_S): a controlled ramp-up on the server clock,
     one session starting every interval run-wide, workspaces interleaved
     (U1-S1, U2-S1, ..., U5-S1, U1-S2, ...). E.g. 200 sessions over 600s =
     one every 3s; with a 20-minute script all 200 then run together from
     ~10 to ~20 minutes. The report shows each session's delay against its
     schedule. Sessions wait (busy, so never suspended) in R for their slot;
   - as ready (MRGSOLVE_START_AT blank): each browser starts its scripts as
     soon as its own sessions are up (like test_mrgsolve_concurrent_batch) -
     the natural ramp-up of users arriving over time, sessions launching
     while others compute. The peak then depends on launch speed against
     script length. (Per browser of ~5 sessions, not per session: a session
     waits at most for the other launches of its browser.)
2. Pacing (optional): MRGSOLVE_ITERATIONS runs of the script per session,
   MRGSOLVE_THINK_TIME_S apart (Sys.sleep in R: the session stays busy, so
   it cannot be suspended meanwhile). Default: one run, no think time.
3. Server timings. The wrapper records every run's server start/end time,
   host node and R process id, in timing.csv in its SESSION_<N> folder and
   in <working dir>/MRGSOLVE_CONCURRENCY_<run tag>/<session name>.csv,
   shared by every workspace of the run (run tag: MRGSOLVE_RUN_TAG, else the
   start epoch). The timing CSV, summary and charts of this test use the
   server times (first start to last end; the client times are kept too).
4. Idle / suspension check (report only, never fails). A session is idle -
   and can be suspended (e.g. after 10 minutes) - only between becoming
   ready and its wrapper being submitted: from then on R is busy (waiting
   for the start time, running, thinking). The report shows each session's
   R uptime at submit (an upper bound on that idle time) against
   MRGSOLVE_IDLE_WARN_S, and flags sessions whose R was restarted
   (suspended and resumed) before their script started. A suspension after
   a session's script has ended, while it waits for the rest of its
   browser, is harmless: its timings are already in the shared folder.
5. One browser's failed launch does not skip the other browsers' sessions.

Two tests:
- test_mrgsolve_r_script_concurrent_new: one workspace's part (run it in
  every workspace with its own MRGSOLVE_SESSION_SUFFIX, e.g. U1..U5).
- test_mrgsolve_concurrency_report: run once after every workspace has
  finished. Reads every run's server timing from the shared folder and
  asserts at least MRGSOLVE_EXPECTED_CONCURRENT runs were running at once,
  all ok.

Settings (besides the MRGSOLVE_* ones of test_mrgsolve_concurrent; all
optional):
    MRGSOLVE_START_AT             spike start: Unix epoch, HH:MM[:SS] (today) or YYYY-MM-DD HH:MM[:SS],
                                  this machine's local time - use an epoch across time zones
    MRGSOLVE_RUN_TAG              name of the shared folder, the same in every workspace (needed for the
                                  report in as-ready mode; default: the start epoch)
    MRGSOLVE_RAMP_UP_S            staggered start: seconds over which every session of the run starts
    MRGSOLVE_START_INTERVAL_S     staggered start: seconds between two starts (when no ramp-up is set)
    MRGSOLVE_WORKSPACE_INDEX      this workspace's number, 1..MRGSOLVE_WORKSPACE_COUNT (default 1)
    MRGSOLVE_WORKSPACE_COUNT      workspaces in the run (default 1)
    MRGSOLVE_ITERATIONS           runs per session (default 1)
    MRGSOLVE_THINK_TIME_S         pause between a session's runs, seconds (default 0)
    MRGSOLVE_IDLE_WARN_S          idle warning limit, seconds (default 480, below a 10-minute suspend timeout)
    MRGSOLVE_EXPECTED_CONCURRENT  runs that must be running at once for the report to pass (default 200)
    MRGSOLVE_NEW_POLL_MS          console check interval, ms (default 5000)

Recipe for 200 sessions from 5 workspaces (same settings except the suffix):
    MRGSOLVE_SESSIONS=40
    MRGSOLVE_SESSION_SUFFIX=U1            # U2 .. U5 in the other workspaces
    spike:    MRGSOLVE_START_AT=2026-11-20 14:30   (far enough ahead to launch 40 sessions)
    as ready: MRGSOLVE_START_AT= and MRGSOLVE_RUN_TAG=20261120a
    staggered: spike settings plus MRGSOLVE_RAMP_UP_S=600, MRGSOLVE_WORKSPACE_COUNT=5 and
               MRGSOLVE_WORKSPACE_INDEX=1 .. 5 (one start every 3s)
    pytest tests/test_mrgsolve_concurrent_new.py -k r_script_concurrent_new -m rstudio_local   # in each workspace
    pytest tests/test_mrgsolve_concurrent_new.py -k concurrency_report -m rstudio_local        # once, afterwards
Headless (no --headed).
"""
import posixpath
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.mrgsolve_concurrency import (
    WrapperWriter,
    concurrency_stats,
    console_timeout_s,
    describe_idle,
    describe_stats,
    format_epoch,
    group_dir,
    idle_summary,
    parse_start_at,
    read_group_timings,
    read_server_timing,
    records_to_rows,
    run_tag,
    start_interval_s,
    start_schedule,
    wrapper_path,
    write_concurrency_report,
)
from common.mrgsolve_summary import report_run_summaries
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.rstudio_console_commands import wait_for_console_ready
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_actions import launch_session_with_retries, login_to_posit_workbench
from common.session_cleanup import quit_sessions_named
from common.session_naming_logic import mrgsolve_run_folder_name, mrgsolve_session_names
from common.session_time_report import describe_run, generate_session_time_report
from common.step_log import log_step
from config.config import env, env_list

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("MRGSOLVE_SESSIONS", "1"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("MRGSOLVE_SESSIONS_BROWSERS") or 0))
RUN_STARTED = time.strftime("%Y%m%d-%H%M%S")
SESSION_SUFFIX = env("MRGSOLVE_SESSION_SUFFIX", "")
# MRGSOLVE_VALIDATION_[<suffix>_]S<N>, the same names as test_mrgsolve_concurrent
SESSION_NAMES = mrgsolve_session_names(SESSION_COUNT, SESSION_SUFFIX)
SESSION_FOLDERS = {name: "SESSION_%d" % (i + 1) for i, name in enumerate(SESSION_NAMES)}
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
SCRIPT_PATH = env("MRGSOLVE_SCRIPT")
WORKING_DIR = env("MRGSOLVE_WORKING_DIR") or (posixpath.dirname(SCRIPT_PATH) if SCRIPT_PATH else None)
RUN_DIR = (posixpath.join(WORKING_DIR, mrgsolve_run_folder_name(SESSION_COUNT, RUN_STARTED, SESSION_SUFFIX))
           if WORKING_DIR else None)
OUTPUT_FILES = env_list("MRGSOLVE_OUTPUT_FILES")
RUN_SUMMARY_FILE = env("MRGSOLVE_RUN_SUMMARY", "")
# Longest one run of the script may take, in minutes
SCRIPT_TIMEOUT_MIN = float(env("MRGSOLVE_SCRIPT_TIMEOUT_MIN") or 30)
# Timing comes from the server, so the consoles need not be polled often -
# which lightens the load of 40-50 tabs on this machine
POLL_INTERVAL_MS = int(env("MRGSOLVE_NEW_POLL_MS", "5000"))
THRESHOLD_S = float(env("MRGSOLVE_THRESHOLD_S") or 0) or None
# Spike start time (blank = as-ready mode: each browser starts when its sessions are up)
START_AT = parse_start_at(env("MRGSOLVE_START_AT", ""))
# Name of the folder every workspace of this run writes its timings to
RUN_TAG = run_tag(env("MRGSOLVE_RUN_TAG", ""), START_AT)
SHARED_DIR = group_dir(WORKING_DIR, RUN_TAG)
# Staggered start (with MRGSOLVE_START_AT): sessions start one every
# interval, run-wide - MRGSOLVE_RAMP_UP_S spread over every session of every
# workspace (600s / 200 = 3s), else MRGSOLVE_START_INTERVAL_S; neither = all at once
WORKSPACE_INDEX = int(env("MRGSOLVE_WORKSPACE_INDEX") or 1)
WORKSPACE_COUNT = int(env("MRGSOLVE_WORKSPACE_COUNT") or 1)
RAMP_UP_S = float(env("MRGSOLVE_RAMP_UP_S") or 0)
INTERVAL_S = start_interval_s(RAMP_UP_S, float(env("MRGSOLVE_START_INTERVAL_S") or 0), SESSION_COUNT * WORKSPACE_COUNT)
SCHEDULE = start_schedule(SESSION_NAMES, START_AT, INTERVAL_S, WORKSPACE_INDEX, WORKSPACE_COUNT) if INTERVAL_S else {}
# Pacing: runs per session and the pause between them
ITERATIONS = max(1, int(env("MRGSOLVE_ITERATIONS") or 1))
THINK_TIME_S = float(env("MRGSOLVE_THINK_TIME_S") or 0)
# R uptime at submit above this is reported (sessions may be suspended after 10 minutes idle)
IDLE_WARN_S = float(env("MRGSOLVE_IDLE_WARN_S") or 480)
# Runs that must be running at once for the concurrency report to pass
EXPECTED_CONCURRENT = int(env("MRGSOLVE_EXPECTED_CONCURRENT", "200"))
MODE = ("staggered" if SCHEDULE else "spike") if START_AT else "as-ready"
TIMINGS_LABEL = "mrgsolve_new_%s_%d_sessions_%d_browsers_U%d%s_%s" % (
    MODE, SESSION_COUNT, BROWSER_COUNT, USER, "_" + SESSION_SUFFIX if SESSION_SUFFIX else "", RUN_STARTED)
REPORT_SESSION_NAME = "MRGSOLVE_VALIDATION_REPORT"


def test_mrgsolve_r_script_concurrent_new(browser_name, browser_type_launch_args, browser_context_args):
    """This workspace's sessions run the mrgsolve script (all at
    MRGSOLVE_START_AT on the server clock, or each browser as soon as its
    sessions are up), timed on the server; idle time before the start and
    R restarts are reported.

    Flow: per browser: login -> quit leftover sessions with its names ->
    launch each in a tab -> session folder -> write its wrapper (noting R's
    uptime and pid) -> source the wrapper (waits for the start time, runs
    the script MRGSOLVE_ITERATIONS times) -> wait -> read its server timing
    and run summary -> check output files -> (all browsers done) close tabs
    -> quit sessions -> reports
    """
    if not SCRIPT_PATH:
        pytest.skip("MRGSOLVE_SCRIPT is not set - set it in .env to the path of run_mrgsolve.R")
    assert SESSION_COUNT >= 1, "MRGSOLVE_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    if START_AT:
        assert START_AT > time.time(), ("MRGSOLVE_START_AT (%s) has already passed - set a time far enough ahead "
                                        "to launch every session in every workspace" % format_epoch(START_AT))
        log_step("Spike mode: the scripts start at %s on the server clock (in %.0fs)",
                 format_epoch(START_AT), START_AT - time.time())
        if SCHEDULE:
            log_step("Staggered start: one session every %.2fs run-wide (workspace %d of %d, %d session(s) each); "
                     "this workspace's sessions start %s .. %s", INTERVAL_S, WORKSPACE_INDEX, WORKSPACE_COUNT,
                     SESSION_COUNT, format_epoch(min(SCHEDULE.values())), format_epoch(max(SCHEDULE.values())))
    else:
        log_step("As-ready mode: each browser starts its scripts as soon as its sessions are up")
    log_step("%d run(s) per session%s; timings shared in %s", ITERATIONS,
             ", %.0fs think time between runs" % THINK_TIME_S if ITERATIONS > 1 and THINK_TIME_S else "",
             SHARED_DIR or "(no shared folder: set MRGSOLVE_RUN_TAG for the cross-workspace report)")
    # The console timeout counts from the submit (before the start time)
    # (counted to the last scheduled start when staggered)
    last_start = max(SCHEDULE.values()) if SCHEDULE else START_AT
    script_timeout_ms = int(console_timeout_s(last_start, SCRIPT_TIMEOUT_MIN * 60, ITERATIONS, THINK_TIME_S) * 1000)
    wrappers = {name: wrapper_path(RUN_DIR, SESSION_FOLDERS[name]) for name in SESSION_NAMES}
    writer = WrapperWriter(SCRIPT_PATH, RUN_DIR, SESSION_FOLDERS, SESSION_SUFFIX, SHARED_DIR, SCHEDULE or START_AT,
                           iterations=ITERATIONS, think_time_s=THINK_TIME_S)

    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, wrappers, RUN_DIR, OUTPUT_FILES, script_timeout_ms,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=False, poll_interval_ms=POLL_INTERVAL_MS,
        # the spike start is synchronised in R: a browser does not wait for (or fail with) the others
        start_together=False,
        summary_file=RUN_SUMMARY_FILE or None, session_folders=SESSION_FOLDERS,
        after_prepare=writer, after_run=read_server_timing,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    report_run_summaries(runs, TIMINGS_LABEL, describe_run(SESSION_COUNT, BROWSER_COUNT, USER), RUN_DIR)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session (mrgsolve script, server clock)",
        threshold=THRESHOLD_S, browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    rows = records_to_rows(runs, writer.info, SESSION_SUFFIX)
    stats = concurrency_stats(rows)
    stats.update(mode=MODE, start_interval_s=INTERVAL_S, iterations_per_session=ITERATIONS,
                 think_time_s=THINK_TIME_S, idle=idle_summary(rows, IDLE_WARN_S))
    where = "this workspace (%s, %s mode)" % (SESSION_SUFFIX or "no suffix", MODE)
    print("\n[rstudio-local] %s: %s" % (where, describe_stats(stats)))
    print("[rstudio-local] %s: %s" % (where, describe_idle(stats["idle"])))
    write_concurrency_report(rows, stats, TIMINGS_LABEL, "This workspace (%s, %s): %d session(s)"
                             % (SESSION_SUFFIX or "no suffix", MODE, SESSION_COUNT), idle_warn_s=IDLE_WARN_S)
    assert not errors, "\n".join(errors)


def test_mrgsolve_concurrency_report(page):
    """Every workspace's server timings of the run (MRGSOLVE_RUN_TAG, else
    MRGSOLVE_START_AT): at least MRGSOLVE_EXPECTED_CONCURRENT runs were
    running at once, and all ended ok. Idle time before the start is
    reported, not asserted.

    Flow: login -> launch MRGSOLVE_VALIDATION_REPORT -> read every run's
    timing from MRGSOLVE_CONCURRENCY_<run tag> -> concurrency and idle
    figures, CSV, JSON and timeline -> quit the session -> assert
    """
    if not SHARED_DIR:
        pytest.skip("set MRGSOLVE_RUN_TAG or MRGSOLVE_START_AT (and MRGSOLVE_WORKING_DIR or MRGSOLVE_SCRIPT) to the "
                    "values the workspaces ran with")
    home_url = login_to_posit_workbench(page, user=USER)
    quit_sessions_named(page, home_url, [REPORT_SESSION_NAME])
    try:
        launch_session_with_retries(page, home_url, REPORT_SESSION_NAME)
        wait_for_console_ready(page)
        rows = read_group_timings(page, SHARED_DIR)
    finally:
        quit_sessions_named(page, home_url, [REPORT_SESSION_NAME])

    stats = concurrency_stats(rows)
    stats["idle"] = idle_summary(rows, IDLE_WARN_S)
    print("\n[rstudio-local] all workspaces (run %s, %s): %s" % (RUN_TAG, SHARED_DIR, describe_stats(stats)))
    print("[rstudio-local] all workspaces: %s" % describe_idle(stats["idle"]))
    for suffix in sorted({r.get("suffix") or "" for r in rows}):
        part = concurrency_stats([r for r in rows if (r.get("suffix") or "") == suffix])
        print("[rstudio-local]   %s: %s" % (suffix or "no suffix", describe_stats(part)))
    write_concurrency_report(rows, stats, "all_workspaces_%s" % RUN_TAG,
                             "All workspaces (run %s): %d session(s)" % (RUN_TAG, stats["sessions"]),
                             idle_warn_s=IDLE_WARN_S)

    assert rows, "no run timings in %s - did the workspaces run with this run tag / start time?" % SHARED_DIR
    failed = sorted("%s run %s: %s" % (r.get("session"), r.get("iteration"), r.get("status"))
                    for r in rows if r.get("status") != "ok")
    assert stats["peak_concurrent"] >= EXPECTED_CONCURRENT, (
        "only %d of the expected %d runs were running at the same time (%d session(s) found). In as-ready mode the "
        "peak depends on launch speed against script length - set MRGSOLVE_EXPECTED_CONCURRENT to what that load "
        "shape allows, or use a spike start (MRGSOLVE_START_AT) to prove N at once"
        % (stats["peak_concurrent"], EXPECTED_CONCURRENT, stats["sessions"]))
    assert not failed, "runs that did not end ok:\n%s" % "\n".join(failed)
