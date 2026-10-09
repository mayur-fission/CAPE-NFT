"""Shared, non-test logic for the tests/ suite: login/setup, the multi-session
*_scenario() flows (cleanup included), result printing, metric payloads and
option parsing. `label` (e.g. "user1") only prefixes printed progress lines.
"""
from common.cloudwatch_metrics import capture_db_metrics, capture_server_metrics
from common.evidence import save_screenshot
from common.rstudio_session_helper import goto_session_list, next_session_names, row_ids
from common.script_timings import (
    new_run_record,
    run_timed_console_command,
    script_timings_csv_path,
    wait_for_console_runs,
    write_script_timings_csv,
)
from common.session_actions import (
    get_console_output,
    launch_session,
    launch_session_with_retries,
    login_to_posit_workbench,
    open_existing_session,
    print_active_session_count,
    set_session_working_directory,
    submit_console_command,
)
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_scenarios import open_sessions_in_tabs


# --- Setup ------------------------------------------------------------------

def login_and_snapshot(page, user=1):
    """Log `page` in as `user` and record the sessions already listed, so
    cleanup_and_verify_sessions() can quit only the ones the test creates.
    Returns (home_url, before_ids).
    """
    home_url = login_to_posit_workbench(page, user=user)
    return home_url, row_ids(page)


def login_and_plan_session_names(page, prefix, count, user=1):
    """login_and_snapshot(), plus the next `count` free <prefix><N> names,
    reserved from one scan of the session list. Returns (home_url,
    before_ids, session_names).
    """
    home_url, before_ids = login_and_snapshot(page, user=user)
    return home_url, before_ids, next_session_names(page, prefix, count, home_url=home_url)


def close_tabs(tabs):
    """Close every tab in `tabs`, ignoring any that are already gone."""
    for tab in tabs:
        try:
            tab.close()
        except Exception:
            pass


# --- Multi-session steps and scenarios ----------------------------------------

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


# --- Results and metrics ------------------------------------------------------

def format_timings(timings):
    """Comma-separated "%.2f" list of timings, for the end-of-test summary."""
    return ", ".join("%.2f" % t for t in timings)


def failed(results):
    """Every result dict whose "ok" is falsy."""
    return [r for r in results if not r["ok"]]


def print_results(results, total, kind, describe):
    """One line per result: "<kind> i/total session=<name>: <describe(r)>" on
    success, "<kind> i/total (<name>) failed: <error>" otherwise."""
    for r in results:
        if r["ok"]:
            print("\n[rstudio-local] %s %d/%d session=%s: %s"
                  % (kind, r["index"], total, r["session_name"], describe(r)))
        else:
            print("\n[rstudio-local] %s %d/%d (%s) failed: %s"
                  % (kind, r["index"], total, r.get("session_name"), r["error"]))


def describe_script_runs(command):
    """Result describer for script runs: launch time plus each run's time."""
    return lambda r: "launch=%.2fs, %d run(s) of %r: %s" % (
        r["launch_elapsed_s"], len(r["run_elapsed_s"]), command,
        ["%.2fs" % e for e in r["run_elapsed_s"]],
    )


def describe_launch_project_text_file(r):
    """Result describer: launch, project and text-file times."""
    return "launch=%.2fs project=%.2fs text_file=%.2fs" % (
        r["launch_elapsed_s"], r["project_elapsed_s"], r["text_file_elapsed_s"])


def describe_launch_project_text_files(r):
    """Result describer: launch and project times plus each text file's."""
    return "launch=%.2fs project=%.2fs text_files=%s" % (
        r["launch_elapsed_s"], r["project_elapsed_s"], ["%.2fs" % s for s in r["text_file_elapsed_s"]])


def describe_launch_project(r):
    """Result describer: launch and project times."""
    return "launch=%.2fs project=%.2fs" % (r["launch_elapsed_s"], r["project_elapsed_s"])


def describe_open_text_files(r):
    """Result describer: open time plus each text file's."""
    return "open=%.2fs text_files=%s" % (
        r["open_elapsed_s"], ["%.2fs" % s for s in r["text_file_elapsed_s"]])


def print_script_result(label, result, command):
    """Print one session's script-run result under `label`."""
    print("\n[rstudio-local] %s session=%s: %s"
          % (label, result["session_name"], describe_script_runs(command)(result)))


def otel_sample(index, elapsed_s, bytes_received=0):
    """One successful-sample dict in the shape capture_otel_metrics() takes."""
    return {"index": index, "ok": True, "elapsed_s": elapsed_s, "bytes_received": bytes_received}


def otel_samples(results, elapsed_s):
    """otel_sample() per result dict, timed by `elapsed_s(result)`."""
    return [otel_sample(r["index"], elapsed_s(r), r["bytes_received"]) for r in results]


def otel_samples_for_runs(result):
    """otel_sample() per run of one session; bytes only count on the first."""
    return [
        otel_sample(i + 1, elapsed, result["bytes_received"] if i == 0 else 0)
        for i, elapsed in enumerate(result["run_elapsed_s"])
    ]


def mean(values):
    """Arithmetic mean of a non-empty sequence."""
    return sum(values) / len(values)


def capture_server_and_db_metrics(minutes):
    """Best-effort CloudWatch server + DB metrics for the last `minutes`."""
    capture_server_metrics(minutes=minutes)
    capture_db_metrics(minutes=minutes)


# --- Option parsing -----------------------------------------------------------

def optional_int(value):
    """int(value), or None when the env value is unset/empty."""
    return int(value) if value else None


def optional_float(value):
    """float(value), or None when the env value is unset/empty."""
    return float(value) if value else None


def is_headless(request):
    """False only when pytest ran with --headed (for browsers the tests open
    themselves, outside the pytest-playwright fixtures)."""
    return not bool(request.config.getoption("--headed", default=False))
