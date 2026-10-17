"""Several sessions one after another on one page, each launch retried.

Flow: login -> launch session 1..N in turn -> (setwd) -> reopen each in a
      tab -> run a command in each -> cleanup
"""
import time

from common.perf_report import allure_step

from config.config import default_session_count
from common.rstudio_session_helper import goto_session_list, row_ids
from common.session_actions import launch_session, set_session_working_directory
from common.session_retry import result_with_retries
from common.session_scenarios import launch_with_project
from common.session_naming_logic import next_auto_perf_session_names
from common.session_actions import launch_session_with_retries
from common.session_actions import print_active_session_count
from common.rstudio_console_commands import get_console_output
from common.script_timings import run_timed_console_command
from common.result_printing import format_timings
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_scenarios import open_sessions_in_tabs
from common.result_printing import failed
from common.script_timings import write_script_timings_csv
from common.script_timings import script_timings_csv_path
from common.rstudio_session_helper import open_existing_session
from common.script_timings import new_run_record
from common.rstudio_console_commands import submit_console_command
from common.evidence import save_screenshot
from common.script_timings import wait_for_console_runs


def _launch_in_turn(page, home_url, count, launch_one):
    """Reserve `count` AUTO_PERF_SESSION_<N> names and run
    launch_one(session_name, index, attempt) for each, with retries.
    Returns (results, run_elapsed_s).
    """
    session_names = next_auto_perf_session_names(page, count, home_url=home_url)
    run_started = time.time()
    results = [
        result_with_retries(i + 1, name, lambda attempt, name=name, i=i: launch_one(name, i + 1, attempt))
        for i, name in enumerate(session_names)
    ]
    return results, time.time() - run_started


def create_multiple_sessions(page, home_url, count=None):
    """Launch `count` sessions (default RSTUDIO_SESSION_COUNT) one after
    another, setting each one's working directory. Never raises on a single
    failure.

    Returns (results, created_ids, run_elapsed_s): one result dict per
    session ("elapsed_s", "bytes_received" on success), the new session-list
    row ids (one before/after diff) and total seconds.
    """
    count = count or default_session_count()
    before_ids = row_ids(page)

    def _launch(session_name, index, attempt):
        with allure_step("launch session %s (%d/%d) (attempt %d)" % (session_name, index, count, attempt)):
            launch = launch_session(page, home_url, session_name=session_name)
            set_session_working_directory(page)
        return {"elapsed_s": launch.elapsed_s, "bytes_received": launch.bytes_received}

    results, run_elapsed = _launch_in_turn(page, home_url, count, _launch)

    # One diff for the whole batch, taken now that every launch has settled.
    goto_session_list(page, home_url, settle_ms=2000)
    return results, row_ids(page) - before_ids, run_elapsed


def create_multiple_sessions_with_projects(page, home_url, count=None, working_dir=None):
    """Launch `count` sessions (default RSTUDIO_SESSION_COUNT) one after
    another, each with a project, so a later step can reopen them by name.
    Never raises on a single failure.

    Returns (results, run_elapsed_s); results carry "launch_elapsed_s",
    "project_elapsed_s" and "bytes_received" on success.
    """
    count = count or default_session_count()

    def _launch(session_name, index, attempt):
        with allure_step("session %s (%d/%d) + project (attempt %d)" % (session_name, index, count, attempt)):
            launch, project = launch_with_project(page, home_url, session_name=session_name, working_dir=working_dir)
        return {
            "launch_elapsed_s": launch.elapsed_s,
            "project_elapsed_s": project.elapsed_s,
            "bytes_received": launch.bytes_received,
        }

    return _launch_in_turn(page, home_url, count, _launch)


# --- Launch sessions, then run a command in each of their tabs -----------------


def close_tabs(tabs):
    """Close every tab in `tabs`, ignoring any that are already gone."""
    for tab in tabs:
        try:
            tab.close()
        except Exception:
            pass


def launch_sessions(page, home_url, session_names, attempts, label, set_working_dir=False):
    """Launch each named session in turn on `page` (with retries). With
    set_working_dir, also setwd to RSTUDIO_WORKING_DIR once per session -
    tabs opened on it later share the same R process. Returns the launch
    times (s).
    """
    timings = []
    for i, session_name in enumerate(session_names):
        launch = launch_session_with_retries(page, home_url, session_name, attempts=attempts, label=label)
        if set_working_dir:
            set_session_working_directory(page)
        timings.append(launch.elapsed_s)
        print("[rstudio-local] %s: %d/%d launched (%s, %.2fs)"
              % (label, i + 1, len(session_names), session_name, launch.elapsed_s))
    return timings


def create_sessions_with_working_dir(page, home_url, session_names):
    """Create each named session in turn and set its working directory,
    printing the Active session count after each. A failed launch is
    recorded, not raised. Returns (timings, created_names, failures)."""
    timings, created, failures = [], [], []
    for i, name in enumerate(session_names):
        try:
            launch = launch_session(page, home_url, session_name=name)
            set_session_working_directory(page)
            timings.append(launch.elapsed_s)
            created.append(launch.session_name)
            print_active_session_count(page, home_url, label="launch %d/%d" % (i + 1, len(session_names)))
        except Exception as exc:
            failures.append((i + 1, str(exc)))
    return timings, created, failures


def _print_console_response(label, i, total, session_name, tab):
    print("[rstudio-local] %s: %d/%d console response (%s):\n%s"
          % (label, i + 1, total, session_name, get_console_output(tab)))


def run_command_in_tabs_sequentially(tabs, session_names, command, timeout_ms, label, script_runs=None):
    """Run `command` in each tab, one at a time, printing each console
    response. Each run is recorded in `script_runs`. Returns the run times
    (s) in tab order."""
    timings = []
    for i, tab in enumerate(tabs):
        elapsed_s = run_timed_console_command(tab, session_names[i], command, timeout_ms, script_runs)
        timings.append(elapsed_s)
        print("[rstudio-local] %s: %d/%d ran %r in tab (%s, %.2fs)"
              % (label, i + 1, len(tabs), command, session_names[i], elapsed_s))
        _print_console_response(label, i, len(tabs), session_names[i], tab)
    return timings


def launch_sessions_scenario(page, home_url, before_ids, session_names, attempts, label):
    """Launch every session and print the launch times; always cleans up."""
    try:
        timings = launch_sessions(page, home_url, session_names, attempts, label)
        print(
            "\n[rstudio-local] %s: launched %d sessions; per-session launch time (s): %s"
            % (label, len(session_names), format_timings(timings))
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids, session_names=session_names)


def run_command_in_tabs_scenario(
    page, context, home_url, before_ids, session_names, attempts, command, timeout_ms, label,
    csv_path=None,
):
    """Launch every session, reopen each in a tab and run `command` in each
    tab one at a time; always closes tabs and cleans up. Writes the script
    timings to `csv_path` (default
    evidence/rstudio_script_timings_<label>_sequential_tabs.csv).

    Returns the sessions that failed to reopen (then no command is run),
    else [].
    """
    tabs, script_runs = [], []
    try:
        launch_timings = launch_sessions(page, home_url, session_names, attempts, label, set_working_dir=True)

        tabs, open_results = open_sessions_in_tabs(context, home_url, session_names)
        open_failures = failed(open_results)
        if open_failures:
            return open_failures

        script_timings = run_command_in_tabs_sequentially(tabs, session_names, command, timeout_ms, label, script_runs)
        print(
            "\n[rstudio-local] %s: launched %d sessions and ran %r in each tab; "
            "launch time (s): %s; script time (s): %s"
            % (label, len(session_names), command, format_timings(launch_timings), format_timings(script_timings))
        )
        return []
    finally:
        write_script_timings_csv(csv_path or script_timings_csv_path("%s_sequential_tabs" % label), script_runs)
        close_tabs(tabs)
        cleanup_and_verify_sessions(page, home_url, before_ids, session_names=session_names)


def run_command_concurrently_in_tabs_scenario(
    page, context, home_url, before_ids, session_names, attempts, command, timeout_ms,
    poll_interval_s, label, csv_path=None,
):
    """Launch every session, then open each in a tab and submit `command`
    without waiting, and monitor all runs together (every poll_interval_s);
    always closes tabs and cleans up. Writes the script timings to
    `csv_path` (default evidence/rstudio_script_timings_<label>_concurrent_tabs.csv),
    even when the run fails.

    Returns "<session> (<error or 'timed out'>)" for every run that did not
    finish, else [].
    """
    tabs, records = [], []
    total = len(session_names)
    try:
        launch_timings = launch_sessions(page, home_url, session_names, attempts, label, set_working_dir=True)
        goto_session_list(page, home_url, settle_ms=0)

        pending = []
        for i, session_name in enumerate(session_names):
            tab = context.new_page()
            tabs.append(tab)
            open_existing_session(tab, home_url, session_name)
            record = new_run_record(session_name)
            records.append(record)
            marker, record["started"] = submit_console_command(tab, command)
            save_screenshot(tab, "console_input_%s.png" % session_name)
            record["status"] = "running"
            pending.append((tab, marker, record, i))
            print("[rstudio-local] %s: %d/%d opened tab and submitted %r (%s) - not waiting for it to finish"
                  % (label, i + 1, total, command, session_name))

        def _on_done(tab, record, i):
            print("[rstudio-local] %s: %d/%d finished %r in tab (%s, %.2fs)"
                  % (label, i + 1, total, command, record["session_name"], record["ended"] - record["started"]))
            _print_console_response(label, i, total, record["session_name"], tab)

        wait_for_console_runs(pending, timeout_ms, int(poll_interval_s * 1000), on_done=_on_done)

        unfinished = ["%s (%s)" % (r["session_name"], r["status"]) for r in records if r["status"] != "ok"]
        if unfinished:
            return unfinished

        print(
            "\n[rstudio-local] %s: launched %d sessions and ran %r concurrently in each tab; "
            "launch time (s): %s; script time (s): %s"
            % (label, total, command, format_timings(launch_timings),
               format_timings([r["ended"] - r["started"] for r in records]))
        )
        return []
    finally:
        write_script_timings_csv(csv_path or script_timings_csv_path("%s_concurrent_tabs" % label), records)
        close_tabs(tabs)
        cleanup_and_verify_sessions(page, home_url, before_ids, session_names=session_names)
