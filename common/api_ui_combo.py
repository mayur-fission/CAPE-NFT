"""Helpers for tests that create sessions through the Workbench API and then
drive them in the browser: create sessions from several CSVs, give groups of
open sessions different workloads (Workbench jobs, repeated console
commands) and clean everything up afterwards.

Run records use the common.script_timings format ("session_name",
"started", "ended", "status").
"""
import posixpath
import time

from common import locators
from common.api_helper import launch_sessions_from_csv, stop_session, write_session_ids
from common.launch_already_created_sessions import close_launched_sessions, launch_already_created_sessions
from common.rstudio_console_commands import is_console_command_done, r_string, submit_console_command
from common.rstudio_workbenchjob import get_job_status, start_workbench_job, stop_workbench_job
from common.script_timings import failed_runs, new_run_record
from common.session_actions import create_folder, login_to_posit_workbench, set_session_working_directory


def ms_left(deadline):
    """Milliseconds until `deadline` (epoch seconds), at least 1000."""
    return max(1000, int((deadline - time.time()) * 1000))


def create_sessions_from_csvs(csv_paths, user, ids_csv_path):
    """launch_sessions_from_csv() for each CSV, then save all their ids
    together to `ids_csv_path` (each call overwrites the file).

    Returns (names_by_csv, created, failed): names_by_csv maps each CSV path
    to the names of the sessions created from it (empty if the CSV has no
    names or all of them failed).
    """
    names_by_csv, created, failed = {}, [], []
    for csv_path in csv_paths:
        csv_created, csv_failed = launch_sessions_from_csv(csv_path, user=user, ids_csv_path=ids_csv_path)
        names_by_csv[csv_path] = [s["session_name"] for s in csv_created]
        created += csv_created
        failed += csv_failed
    write_session_ids(ids_csv_path, created)
    return names_by_csv, created, failed


def open_sessions(context, user, session_names):
    """Log in as `user` and open each session in its own tab. Returns the
    LaunchedSession list; if any session fails to open, closes the tabs and
    raises AssertionError.
    """
    home_url = login_to_posit_workbench(context.new_page(), user=user)
    launched = launch_already_created_sessions(context, home_url, session_names)
    not_opened = ["%s: %s" % (s.session_name, s.error) for s in launched if s.error]
    if not_opened:
        close_launched_sessions(launched)
        raise AssertionError("sessions that could not be opened: %s" % not_opened)
    return launched


def report_failed_runs(runs, label, started, idle_sessions=()):
    """Print a one-line summary of `runs` (and of the sessions left idle) and
    return failed_runs(runs)."""
    failures = failed_runs(runs)
    print(
        "\n[rstudio-local] %s: %d of %d run(s) finished successfully, %d session(s) left idle (%s), %.2fs in total"
        % (label, len(runs) - len(failures), len(runs), len(idle_sessions),
           ", ".join(s.session_name for s in idle_sessions), time.time() - started)
    )
    return failures


def clean_up(launched, created, user, job_sessions=(), job_script_path=None):
    """Stop the user's running `job_script_path` Workbench jobs (from one of
    `job_sessions`' tabs), close the tabs in `launched` and quit every
    session in `created`. Errors are printed, not raised.
    """
    job_pages = [s.page for s in job_sessions if s.page is not None]
    if job_pages and job_script_path:
        stop_workbench_jobs(job_pages[0], posixpath.basename(job_script_path))
    close_launched_sessions(launched)
    quit_sessions(created, user)


def quit_sessions(created, user):
    """Quit every session in `created` (from create_sessions_from_csvs())
    through the API. Errors are printed, not raised.
    """
    ids = [s["session_id"] for s in created]
    if not ids:
        return
    try:
        stop_session(ids, user=user, force_quit=True)
        print("\n[rstudio-local] quit %d session(s): %s"
              % (len(ids), ", ".join(s["session_name"] for s in created)))
    except Exception as exc:
        print("\n[rstudio-local] could not quit sessions %s: %s" % (ids, exc))


def start_workbench_jobs(sessions, script_path, output_dir):
    """setwd each session to `output_dir`/<session name> (created if missing)
    and start `script_path` as a Workbench job. Returns (records, started)
    where started holds (page, job_name, record) for each job that started.
    """
    records, started = [], []
    for session in sessions:
        record = new_run_record(session.session_name)
        records.append(record)
        try:
            session_dir = posixpath.join(output_dir, session.session_name)
            create_folder(session.page, session_dir)
            set_session_working_directory(session.page, path=session_dir)
            record["started"] = time.time()
            job_name, _ = start_workbench_job(session.page, script_path)
            started.append((session.page, job_name, record))
            print("\n[rstudio-local] session %s: started Workbench job %s" % (session.session_name, job_name))
        except Exception as exc:
            record["status"] = "job start failed: %s" % exc
    return records, started


def check_workbench_jobs(started):
    """Mark each job from start_workbench_jobs() "ok" unless it ended as
    Failed, Canceled or Killed. The script may loop forever, so a job that
    is still running counts as ok; stop_workbench_jobs() stops it.
    """
    for page, job_name, record in started:
        try:
            status = get_job_status(page, job_name) or "not listed"
        except Exception as exc:
            record["status"] = "job status unknown: %s" % exc
            continue
        record["ended"] = time.time()
        failed = any(state in status for state in locators.WORKBENCH_JOB_FAILED_STATES)
        record["status"] = "job %s" % status if failed else "ok"
        print(
            "\n[rstudio-local] session %s: Workbench job %s is %r after %.2fs"
            % (record["session_name"], job_name, status, record["ended"] - record["started"])
        )


def stop_workbench_jobs(page, job_name, max_jobs=10):
    """Stop every still-running `job_name` job of the user (the Workbench
    Jobs pane lists all of them), so none outlive the test. Errors are
    printed, not raised.
    """
    stopped = 0
    try:
        while stopped < max_jobs and stop_workbench_job(page, job_name):
            stopped += 1
    except Exception as exc:
        print("\n[rstudio-local] could not stop Workbench job %s: %s" % (job_name, exc))
    print("\n[rstudio-local] stopped %d running Workbench job(s) %s" % (stopped, job_name))


class ListFilesLoop:
    """Send list.files(`path`) to each session's console; once it has
    answered, wait `interval_s` and send it again, until `deadline`.
    Call tick() regularly (e.g. as the on_poll of
    run_script_in_launched_sessions()), then run_until_deadline() to keep
    going until the end. Each call is one record in `records`.
    """

    def __init__(self, sessions, path, deadline, interval_s=30):
        self.deadline = deadline
        self.interval_s = interval_s
        self.records = []
        self.command = "list.files(%s)" % r_string(path)
        # session -> (marker, record) while a call is in flight, else None
        self.in_flight = {session: None for session in sessions}
        self.next_due = {session: time.time() for session in sessions}

    def tick(self):
        now = time.time()
        for session, pending in self.in_flight.items():
            if pending:
                self._check(session, *pending)
            elif self.next_due[session] <= now < self.deadline:
                record = new_run_record(session.session_name)
                self.records.append(record)
                try:
                    marker, record["started"] = submit_console_command(session.page, self.command)
                    self.in_flight[session] = (marker, record)
                except Exception as exc:
                    record["status"] = "list.files failed: %s" % exc
                    self.next_due[session] = now + self.interval_s

    def _check(self, session, marker, record):
        try:
            done = is_console_command_done(session.page, marker)
        except Exception as exc:
            record["status"] = "list.files failed: %s" % exc
            done = True
        else:
            if done:
                record["ended"], record["status"] = time.time(), "ok"
                print(
                    "\n[rstudio-local] session %s: %s answered in %.2fs"
                    % (session.session_name, self.command, record["ended"] - record["started"])
                )
        if done:
            self.in_flight[session] = None
            self.next_due[session] = time.time() + self.interval_s

    def run_until_deadline(self, poll_interval_s=0.5, grace_s=60):
        """Keep calling until `deadline`, then wait up to grace_s for calls
        still in flight (the rest are marked timed out). Returns `records`.
        """
        left_s = self.deadline - time.time()
        if left_s > 0:
            print("\n[rstudio-local] running until the end of the test, %.0fs more" % left_s)
        while time.time() < self.deadline:
            self.tick()
            time.sleep(min(poll_interval_s, max(0, self.deadline - time.time())))
        until = time.time() + grace_s
        while any(self.in_flight.values()) and time.time() < until:
            self.tick()
            time.sleep(poll_interval_s)
        for pending in self.in_flight.values():
            if pending:
                pending[1]["status"] = "timed out"
        return self.records
