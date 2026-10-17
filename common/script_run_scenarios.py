"""R script / Workbench job in several sessions, one tab each.

Flow (new sessions): login -> reserve names -> launch in tabs -> setwd ->
      source script in all -> wait -> check outputs -> quit sessions
Flow (existing sessions): login -> open each in a tab -> setwd -> source
      script in all -> wait -> check outputs -> close tabs
Flow (close tabs): launch + source script -> close tabs -> wait / watch for
      drops -> stop scripts -> quit sessions
"""
import posixpath
import time

from common.session_actions import login_and_plan_session_names, login_and_snapshot
from common.launch_already_created_sessions import (
    close_launched_sessions,
    launch_already_created_sessions,
    run_script_in_launched_sessions,
)
from common.script_timings import failed_runs, script_timings_csv_path, write_script_timings_csv
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_monitor import monitor_sessions, stop_console_scripts
from common.session_naming_logic import AUTO_PERF_NAME_PREFIX
from common.result_printing import print_script_runs_summary
from common.rstudio_session_helper import launch_new_session
from common.rstudio_workbenchjob import start_workbench_job
from common.evidence import save_screenshot
from common.rstudio_workbenchjob import get_job_status
from common.rstudio_session_helper import open_existing_session
from common.rstudio_workbenchjob import stop_workbench_job
from common.rstudio_console_commands import r_source_command
from common.session_actions import set_session_working_directory
from common.rstudio_console_commands import submit_console_command
from common.rstudio_console_commands import r_string
from common.script_timings import new_run_record
from common.rstudio_files import close_all_editor_files
from common.rstudio_files import recreate_session_folder
from common.script_outputs import check_output_file
from common.script_outputs import report_output_file
from common.script_timings import wait_for_console_runs
from common.script_timings import submit_in_each
from common import locators


def run_script_in_new_sessions_scenario(context, session_count, label, script_path, working_dir,
                                        output_file_name, timeout_ms, per_session_dir=False,
                                        name_prefix=AUTO_PERF_NAME_PREFIX):
    """Run `script_path` in `session_count` new <name_prefix><N> sessions at
    once; a run is ok if it finishes within `timeout_ms` and leaves
    `output_file_name` (then deleted).

    Flow: login -> reserve names -> launch in tabs -> setwd -> source script in
          all -> wait -> check/delete outputs -> timings CSV -> quit sessions

    Cleanup quits only these names; its error is raised only if nothing else
    failed. Returns failed_runs() of the runs.
    """
    home_page = context.new_page()
    home_url, before_ids, session_names = login_and_plan_session_names(home_page, name_prefix, session_count)
    runs, failures = [], None

    started = time.time()
    try:
        runs = launch_sessions_and_run_script(
            context, home_url, session_count, script_path,
            working_dir=working_dir, timeout_ms=timeout_ms,
            output_path=output_file_name, per_session_dir=per_session_dir, delete_output=True,
            session_names=session_names,
        )
        print_script_runs_summary(label, runs, script_path, started)
        failures = failed_runs(runs)
    finally:
        write_script_timings_csv(script_timings_csv_path(label), runs)
        try:
            cleanup_and_verify_sessions(home_page, home_url, before_ids, session_names=session_names)
        except Exception as cleanup_exc:
            if failures == []:
                raise
            print("\n[rstudio-local] %s: cleanup also failed: %s" % (label, cleanup_exc))
    return failures


def run_script_in_existing_sessions_scenario(context, session_names, label, script_path, working_dir,
                                             output_file_name, timeout_ms, user=1, delete_output=False):
    """Log in as `user`, open each existing session in `session_names` in its
    own tab, setwd to `working_dir`/<session name> (created if missing) and
    source `script_path` in every console at once. A run is "ok" only if it
    finishes within `timeout_ms` and leaves `output_file_name` in its folder;
    with delete_output the file (and an emptied folder) is then removed.

    The sessions keep running; only the tabs are closed. Writes the timings
    CSV. Returns (not_opened, failures): sessions that could not be opened
    (the script is not run when any are) and the failed runs.
    """
    home_url = login_to_posit_workbench(context.new_page(), user=user)
    launched, runs = [], []

    started = time.time()
    try:
        launched = launch_already_created_sessions(context, home_url, session_names)
        not_opened = ["%s: %s" % (s.session_name, s.error) for s in launched if s.error]
        if not_opened:
            return not_opened, []

        runs = run_script_in_launched_sessions(
            launched, script_path, working_dir=working_dir, timeout_ms=timeout_ms,
            output_path=output_file_name, per_session_dir=True, delete_output=delete_output,
        )
        print_script_runs_summary(label, runs, script_path, started)
        return [], failed_runs(runs)
    finally:
        write_script_timings_csv(script_timings_csv_path(label), runs)
        close_launched_sessions(launched)


def start_jobs_close_tabs_then_stop_jobs_scenario(context, session_count, script_path, wait_s):
    """Launch `session_count` new sessions, each in its own tab, start
    `script_path` as a Workbench job and close the tab once it has started.
    After `wait_s`, reopen every session and stop its running job. Quits
    every session created, even on failure. Returns the reopen/stop failures.
    """
    home_page = context.new_page()
    home_url, before_ids = login_and_snapshot(home_page)

    try:
        session_names = launch_sessions_and_start_jobs(context, home_url, session_count, script_path)

        print("\n[rstudio-local] all tabs closed, waiting %.0fs before reopening sessions" % wait_s)
        home_page.wait_for_timeout(wait_s * 1000)

        return reopen_sessions_and_stop_jobs(home_page, home_url, session_names, posixpath.basename(script_path))
    finally:
        cleanup_and_verify_sessions(home_page, home_url, before_ids)


def source_script_close_tabs_then_quit_scenario(context, session_count, script_path, wait_s,
                                                monitor_interval_s=None, stop_before_cleanup=False,
                                                label="close_tab", name_prefix=None):
    """Sessions run a console script with their tabs closed, then are quit.

    Flow: login (reserve <name_prefix><N>, else AUTO_PERF_SESSION_<N>) -> launch
          each in a tab -> source script -> close tab -> wait `wait_s` (or watch
          for drops every monitor_interval_s) -> (stop_before_cleanup: reopen
          each and interrupt its script) -> quit sessions

    After cleanup an AssertionError lists dropped sessions and scripts that
    could not be stopped. Returns the ids of the sessions quit.
    """
    home_page = context.new_page()
    planned_names = None
    if name_prefix:
        home_url, before_ids, planned_names = login_and_plan_session_names(home_page, name_prefix, session_count)
    else:
        home_url, before_ids = login_and_snapshot(home_page)
    quit_ids = None
    drops, stop_failures = [], {}

    try:
        session_names = launch_sessions_and_source_script(context, home_url, session_count, script_path,
                                                          session_names=planned_names)

        if monitor_interval_s:
            print("\n[rstudio-local] all tabs closed, watching the sessions for %.0fs before quitting them" % wait_s)
            drops = monitor_sessions(home_page, home_url, session_names, wait_s, monitor_interval_s, label)
        else:
            print("\n[rstudio-local] all tabs closed, waiting %.0fs before quitting sessions" % wait_s)
            home_page.wait_for_timeout(wait_s * 1000)

        if stop_before_cleanup:
            dropped = {d["session"] for d in drops}
            stop_failures = stop_console_scripts(context, home_url, [n for n in session_names if n not in dropped])

        started = time.time()
        quit_ids = cleanup_and_verify_sessions(home_page, home_url, before_ids, session_names=planned_names)
        print("\n[rstudio-local] quit %d session(s) in %.2fs" % (len(quit_ids), time.time() - started))
    finally:
        if quit_ids is None:
            cleanup_and_verify_sessions(home_page, home_url, before_ids, session_names=planned_names)

    problems = ["dropped while its tab was closed: %s (%s after %ds)" % (d["session"], d["status"], d["seconds_after_start"])
                for d in drops]
    problems += ["script could not be stopped: %s (%s)" % item for item in stop_failures.items()]
    assert not problems, "\n".join(problems)
    return quit_ids


# --- Launch several sessions in tabs and run a script / job in each ------------


def launch_sessions_and_start_jobs(context, home_url, session_count, script_path):
    """Launch `session_count` new sessions, each in its own tab, start
    `script_path` as a Workbench job in each and close the tab as soon as the
    job has started. Returns the names of the sessions launched, including
    one whose job failed to start.
    """
    session_names = []
    for _ in range(session_count):
        tab = context.new_page()
        try:
            launch = launch_new_session(tab, home_url)
            session_names.append(launch.session_name)
            job_name, submit_elapsed = start_workbench_job(tab, script_path)
            save_screenshot(tab, "workbench_job_started_%s.png" % launch.session_name)
            print(
                "\n[rstudio-local] session %s launched in %.2fs, job %s started in %.2fs (status: %s)"
                % (launch.session_name, launch.elapsed_s, job_name, submit_elapsed,
                   get_job_status(tab, job_name))
            )
        finally:
            tab.close()
    return session_names


def reopen_sessions_and_stop_jobs(page, home_url, session_names, job_name):
    """Reopen each session in `page` and stop a running `job_name` job.
    Returns a list of failure messages (empty if every job was stopped).
    """
    failures = []
    for session_name in session_names:
        try:
            reopen = open_existing_session(page, home_url, session_name)
            stopped = stop_workbench_job(page, job_name)
            print(
                "\n[rstudio-local] session %s reopened in %.2fs, %s job %s (status: %s)"
                % (session_name, reopen.elapsed_s, job_name,
                   "stopped" if stopped else "was not running", get_job_status(page, job_name))
            )
            if not stopped:
                failures.append("%s: no running %s job to stop" % (session_name, job_name))
        except Exception as exc:
            failures.append("%s: %s" % (session_name, exc))
    return failures


def launch_sessions_and_source_script(context, home_url, session_count, script_path,
                                      working_dir=None, session_names=None):
    """Launch `session_count` new sessions, each in its own tab, setwd to
    `working_dir` (default RSTUDIO_WORKING_DIR; skipped if neither is set),
    source `script_path` in the console and close the tab without waiting for
    the script to finish. The sessions are named `session_names` when given,
    else the next AUTO_PERF_SESSION_<N>. Returns the names launched.
    """
    source_command = r_source_command(script_path)
    planned = list(session_names) if session_names else [None] * session_count
    session_names = []
    for planned_name in planned:
        tab = context.new_page()
        try:
            launch = launch_new_session(tab, home_url, session_name=planned_name)
            session_names.append(launch.session_name)
            set_session_working_directory(tab, path=working_dir)
            submit_console_command(tab, source_command)
            # The console echoes the command once R has accepted it.
            tab.locator(locators.CONSOLE_OUTPUT_SELECTOR).get_by_text(source_command).first.wait_for(
                state="visible", timeout=10000
            )
            save_screenshot(tab, "console_input_%s.png" % launch.session_name)
            print(
                "\n[rstudio-local] session %s launched in %.2fs, %s submitted in the console"
                % (launch.session_name, launch.elapsed_s, source_command)
            )
        finally:
            tab.close()
    return session_names


def launch_sessions_and_run_script(context, home_url, session_count, script_path,
                                   working_dir=None, timeout_ms=1800000, poll_interval_ms=500, output_path=None,
                                   per_session_dir=False, delete_output=False, session_names=None):
    """Launch `session_count` new sessions in tabs and run `script_path` in
    all of them at once.

    Flow: launch each (names `session_names`, else AUTO_PERF_SESSION_<N>) ->
          setwd (per_session_dir: own emptied `working_dir`/<session>) ->
          source script in all -> wait -> check `output_path` (and delete it) ->
          close tabs

    Returns one run record per session (common.script_timings).
    """
    if per_session_dir and not working_dir:
        raise ValueError("per_session_dir needs a working_dir")
    # Source into a throwaway environment: scripts that leave very many
    # objects in the global environment (sample_100mb.R) keep R busy for
    # minutes refreshing RStudio's Environment pane after they return, so
    # the output-file check below never gets a reply. The start/finish
    # messages show in the console when the script began and returned.
    source_command = (
        "message('Script execution started at: ', Sys.time()); "
        "source(%s, local=new.env()); "
        "message('Script execution finished at: ', Sys.time())" % r_string(script_path)
    )
    tabs, runs, ready = [], [], []
    try:
        for i in range(session_count):
            tab = context.new_page()
            tabs.append(tab)
            name = session_names[i] if session_names else None
            record = new_run_record(name or "session %d" % (i + 1))
            runs.append(record)
            try:
                launch = launch_new_session(tab, home_url, session_name=name)
                record["session_name"] = launch.session_name
                close_all_editor_files(tab, launch.session_name)
                session_dir = posixpath.join(working_dir, launch.session_name) if per_session_dir else None
                if session_dir:
                    recreate_session_folder(tab, working_dir, launch.session_name)
                set_session_working_directory(tab, path=session_dir or working_dir)
                ready.append((tab, record, session_dir))
                print("\n[rstudio-local] session %s launched in %.2fs" % (launch.session_name, launch.elapsed_s))
            except Exception as exc:
                record["status"] = "launch failed: %s" % exc

        def _on_done(tab, record, session_dir):
            print("\n[rstudio-local] session %s: %s took %.2fs"
                  % (record["session_name"], script_path, record["ended"] - record["started"]))
            if output_path:
                check_output_file(tab, record, output_path, delete=delete_output, remove_dir=session_dir)

        def _on_timeout(tab, record, session_dir):
            if output_path:
                report_output_file(tab, record, output_path)

        wait_for_console_runs(
            submit_in_each(ready, source_command), timeout_ms, poll_interval_ms,
            on_done=_on_done, on_timeout=_on_timeout,
        )
    finally:
        for tab in tabs:
            tab.close()
    return runs
