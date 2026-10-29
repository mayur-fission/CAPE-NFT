"""A mix of workloads in many sessions at once: sessions spread over
browsers (5 tabs each), all logged in as one user
(common/concurrent_file_crud.py's flow). Every 5 sessions are, in this
order (SCENARIO_1_MIX):
  - file crud:   text file create / open / edit / delete loops
                 (common/text_file_crud.py), TEXT_FILE_LOOP_WAIT_S apart
  - idle:        tab just kept open, checked for drops (common/concurrent_idle.py)
  - small 10min: R_SCRIPT_SMALL_10MIN sourced once, in its own folder, its
                 output files checked (test_concurrent_sessions_10min_s)
  - load files x2: a console command (default list.files(<working dir>))
                 repeated LOAD_FILES_WAIT_S after each answer
                 (common/concurrent_load_files.py)
so 5 consecutive sessions - one browser's tabs - hold one of each. Once
every browser is ready, all workloads start together and run until the
duration is up (the script runs to its end, however long it takes).

A browser runs in one thread, so its tabs take turns: the script's console
is polled between the other workloads' steps, and a file loop (seconds of
mouse and keyboard) delays the next poll - the script's end can be seen up
to one file loop late. Its run summary's runtime is read from the script
itself, unaffected.

Session names: SCENARIO1_<workload>_<N>, e.g. SCENARIO1_IDLE_2.
"""
import os
import posixpath
import threading
import time

from common.api_ui_combo import ListFilesLoop
from common.concurrent_file_crud import FileLoops, run_loops_in_concurrent_sessions
from common.concurrent_idle import IdleWatch
from common.concurrent_load_files import CALL_GRACE_S
from common.launch_already_created_sessions import prepare_session_folders, run_script_in_launched_sessions
from common.perf_report import attach_allure_file, write_json_report
from common.rstudio_console_commands import wait_for_console_ready
from common.script_timings import failed_runs
from common.session_actions import set_session_working_directory
from common.session_naming_logic import SCENARIO_1_SESSION_PREFIX
from common.step_log import log_step
from common.text_file_crud import STEP_TIMEOUT_MS, prepare_session
from config.config import evidence_dir

FILE_CRUD = "FILE_CRUD"
IDLE = "IDLE"
SMALL_10MIN = "SMALL_10MIN"
LOAD_FILES = "LOAD_FILES"
# One browser's 5 tabs: 1 file crud, 1 idle, 1 small 10min script, 2 load files
SCENARIO_1_MIX = (FILE_CRUD, IDLE, SMALL_10MIN, LOAD_FILES, LOAD_FILES)
# How often the other workloads are driven once the script has finished
TICK_S = 0.5


def scenario_session_names(count, mix=SCENARIO_1_MIX, prefix=SCENARIO_1_SESSION_PREFIX):
    """`count` names following `mix` over and over, each numbered within its
    workload: SCENARIO1_FILE_CRUD_1, SCENARIO1_IDLE_1, SCENARIO1_SMALL_10MIN_1,
    SCENARIO1_LOAD_FILES_1, SCENARIO1_LOAD_FILES_2, SCENARIO1_FILE_CRUD_2, ..."""
    numbers, names = {}, []
    for i in range(count):
        kind = mix[i % len(mix)]
        numbers[kind] = numbers.get(kind, 0) + 1
        names.append("%s%s_%d" % (prefix, kind, numbers[kind]))
    return names


def workload_of(session_name, prefix=SCENARIO_1_SESSION_PREFIX):
    """The workload in a scenario_session_names() name."""
    return session_name[len(prefix):].rsplit("_", 1)[0]


def run_scenario_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                        browser_count, label, duration_s, crud_wait_s, command, call_name,
                                        call_wait_s, idle_check_interval_s, script_path, output_dir,
                                        output_files, script_timeout_ms, poll_interval_ms=500,
                                        delete_output=True, summary_file=None, stagger_s=0, attempts=3,
                                        launch_timeout_ms=180000, retry_wait_s=15):
    """Run every session's workload (workload_of() its name) in
    `session_names`, spread over `browser_count` browsers (started
    `stagger_s` apart), from the moment all are ready until `duration_s`
    later.

    Returns (records, drops, errors): the run records of every workload
    (file steps, command calls, idle holds, script runs - "script" tells
    them apart), the idle sessions' drops (concurrent_idle.IdleWatch) and
    "browser <B>: <error>" per browser with a failure or failed launch.
    """
    run_stamp = time.strftime("%Y%m%d-%H%M%S")
    script = posixpath.basename(script_path or "")
    prepared_scripts = {}  # session name -> prepare_session_folders() result
    drops, lock = [], threading.Lock()

    def _prepare(session):
        kind = workload_of(session.session_name)
        if kind == FILE_CRUD:
            prepare_session(session.page, session.session_name)
        elif kind == SMALL_10MIN:
            wait_for_console_ready(session.page)
            prepared = prepare_session_folders([session], output_dir, per_session_dir=True)
            with lock:
                prepared_scripts.update(prepared)
        elif kind == LOAD_FILES:
            wait_for_console_ready(session.page)
            set_session_working_directory(session.page)
        else:
            wait_for_console_ready(session.page)
        log_step("Session %s ready for its %s workload", session.session_name, kind.lower())

    def _run_workloads(launched, records, home_url):
        by_kind = {}
        for session in launched:
            by_kind.setdefault(workload_of(session.session_name), []).append(session)
        log_step("Running the workloads: %s", ", ".join(
            "%d %s" % (len(by_kind.get(k, [])), k.lower()) for k in dict.fromkeys(SCENARIO_1_MIX)))
        deadline = time.time() + duration_s
        crud = (FileLoops(by_kind[FILE_CRUD], run_stamp, deadline, crud_wait_s, records)
                if FILE_CRUD in by_kind else None)
        calls = (ListFilesLoop(by_kind[LOAD_FILES], None, deadline, call_wait_s, command=command, script=call_name)
                 if LOAD_FILES in by_kind else None)
        idle = (IdleWatch(by_kind[IDLE], records, home_url, deadline, idle_check_interval_s,
                          "%s_%s" % (label, by_kind[IDLE][0].session_name))
                if IDLE in by_kind else None)

        def _tick_others():
            if crud is not None:
                crud.tick()
            if calls is not None:
                calls.tick()
            if idle is not None:
                idle.tick()

        failures = []
        try:
            if SMALL_10MIN in by_kind:
                runs = run_script_in_launched_sessions(
                    by_kind[SMALL_10MIN], script_path, working_dir=output_dir, timeout_ms=script_timeout_ms,
                    output_path=output_files, per_session_dir=True, delete_output=delete_output,
                    poll_interval_ms=poll_interval_ms, prepared=prepared_scripts, summary_file=summary_file,
                    on_poll=_tick_others,
                )
                for run in runs:
                    run["script"] = script
                records += runs
                failures += failed_runs(runs)
                log_step("Script %s done in %d session(s) - the other workloads carry on until the duration is up",
                         script, len(runs))
            while time.time() < deadline:
                _tick_others()
                time.sleep(min(TICK_S, max(0.0, deadline - time.time())))
            if calls is not None:
                calls.run_until_deadline(grace_s=CALL_GRACE_S)
            if idle is not None:
                idle.tick()  # the last round, now the duration is up
        finally:
            if calls is not None:
                records += calls.records
            if idle is not None:
                idle.finish()
                with lock:
                    drops.extend(idle.drops)
        if crud is not None:
            failures += crud.failures
        if calls is not None:
            failures += ["%s call %d: %s" % (r["session_name"], r["loop"], r["status"])
                         for r in calls.records if r["status"] != "ok"]
        if idle is not None:
            failures += ["%s: %s" % (d["session"], d["status"]) for d in idle.drops]
        return failures

    # a file loop can take a few step timeouts on top of the duration
    loop_stage_s = (max(duration_s, script_timeout_ms / 1000.0) + 4 * 3 * STEP_TIMEOUT_MS / 1000.0
                    + CALL_GRACE_S)
    records, errors = run_loops_in_concurrent_sessions(
        browser_name, launch_args, context_args, user, session_names, browser_count, label,
        "scenario 1 workloads (for %.0fs)" % duration_s, _prepare, _run_workloads, loop_stage_s,
        stagger_s=stagger_s, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
        retry_wait_s=retry_wait_s, with_home_url=True,
    )
    drops.sort(key=lambda d: d["seconds_after_idle_start"])
    if drops:
        json_path = os.path.join(evidence_dir(), "session_drops_%s.json" % label)
        write_json_report(json_path, drops)
        attach_allure_file(json_path)
    return records, drops, errors


def workload_summary(records):
    """One line per workload: its sessions, ok / failed records and the
    min / avg / max time of the ok ones."""
    lines = []
    for kind in dict.fromkeys(SCENARIO_1_MIX):
        mine = [r for r in records if workload_of(r["session_name"]) == kind]
        times = [r["ended"] - r["started"] for r in mine
                 if r["status"] == "ok" and r.get("started") is not None and r.get("ended") is not None]
        ok = len(times)
        line = "  %-11s %d session(s), %d ok / %d failed record(s)" % (
            kind.lower(), len({r["session_name"] for r in mine}), ok, len(mine) - ok)
        if times:
            line += ", min %.2f / avg %.2f / max %.2f s" % (min(times), sum(times) / len(times), max(times))
        lines.append(line)
    return "\n".join(lines)
