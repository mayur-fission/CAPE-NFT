"""Full test flows for sourcing an R script in several sessions, one tab per
session: new sessions (quit afterwards) or already existing ones (left
running, only their tabs are closed). Every flow writes its per-run timings
to evidence/rstudio_script_timings_<label>.csv and returns the failures for
the test to assert on.

Run records use the common.script_timings format ("session_name",
"started", "ended", "status").
"""
import posixpath
import time

from common.helper_function import login_and_snapshot
from common.launch_already_created_sessions import (
    close_launched_sessions,
    launch_already_created_sessions,
    run_script_in_launched_sessions,
)
from common.rstudio_workbenchjob import (
    launch_sessions_and_run_script,
    launch_sessions_and_source_script,
    launch_sessions_and_start_jobs,
    reopen_sessions_and_stop_jobs,
)
from common.script_timings import failed_runs, script_timings_csv_path, write_script_timings_csv
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions


def print_script_runs_summary(label, runs, script_path, started):
    """One line: how many of `runs` succeeded and the time since `started`."""
    failures = failed_runs(runs)
    print(
        "\n[rstudio-local] %s: %d of %d session(s) ran %s successfully, %.2fs in total"
        % (label, len(runs) - len(failures), len(runs), script_path, time.time() - started)
    )


def run_script_in_new_sessions_scenario(context, session_count, label, script_path, working_dir,
                                        output_file_name, timeout_ms, per_session_dir=False):
    """Launch `session_count` new sessions, each in its own tab, setwd to
    `working_dir` (`working_dir`/<session name> with per_session_dir) and
    source `script_path` in every console at once. A run is "ok" only if it
    finishes within `timeout_ms` and leaves `output_file_name` behind; the
    file (~100 MB for the high-throughput script) is then deleted, along with
    any per-session folder.

    Writes the timings CSV and quits every session created, even on failure.
    A cleanup error is raised only if nothing else failed, so it never hides
    the original problem. Returns failed_runs() of the runs.
    """
    home_page = context.new_page()
    home_url, before_ids = login_and_snapshot(home_page)
    runs, failures = [], None

    started = time.time()
    try:
        runs = launch_sessions_and_run_script(
            context, home_url, session_count, script_path,
            working_dir=working_dir, timeout_ms=timeout_ms,
            output_path=output_file_name, per_session_dir=per_session_dir, delete_output=True,
        )
        print_script_runs_summary(label, runs, script_path, started)
        failures = failed_runs(runs)
    finally:
        write_script_timings_csv(script_timings_csv_path(label), runs)
        try:
            cleanup_and_verify_sessions(home_page, home_url, before_ids)
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


def source_script_close_tabs_then_quit_scenario(context, session_count, script_path, wait_s):
    """Launch `session_count` new sessions, each in its own tab, source
    `script_path` in the console and close the tab without waiting for it.
    After `wait_s`, quit every session created, which ends the script.
    Returns the ids of the sessions quit.
    """
    home_page = context.new_page()
    home_url, before_ids = login_and_snapshot(home_page)
    quit_ids = None

    try:
        launch_sessions_and_source_script(context, home_url, session_count, script_path)

        print("\n[rstudio-local] all tabs closed, waiting %.0fs before quitting sessions" % wait_s)
        home_page.wait_for_timeout(wait_s * 1000)

        started = time.time()
        quit_ids = cleanup_and_verify_sessions(home_page, home_url, before_ids)
        print("\n[rstudio-local] quit %d session(s) in %.2fs" % (len(quit_ids), time.time() - started))
        return quit_ids
    finally:
        if quit_ids is None:
            cleanup_and_verify_sessions(home_page, home_url, before_ids)
