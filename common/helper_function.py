"""Shared, non-test logic for the tests/ suite: result printing, metric
payloads, option parsing, and the multi-session scenario steps. The
*_scenario() functions chain steps into a test's full flow, cleanup included.
`label` (e.g. "user1") only prefixes printed progress lines.
"""
import time

from common.cloudwatch_metrics import capture_db_metrics, capture_server_metrics
from common.script_timings import (
    run_timed_console_command,
    script_timings_csv_path,
    write_script_timings_csv,
)
from common.session_actions import (
    is_console_command_done,
    launch_session,
    get_console_output,
    launch_session_with_retries,
    open_existing_session,
    print_active_session_count,
    set_session_working_directory,
    submit_console_command,
)
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_scenarios import open_sessions_in_tabs


def format_timings(timings):
    """Comma-separated "%.2f" list of timings, for the end-of-test summary."""
    return ", ".join("%.2f" % t for t in timings)


def launch_sessions(page, home_url, session_names, attempts, label, set_working_dir=False):
    """Launches each session in turn (with retries); returns launch times (s).
    `set_working_dir` sets it once per session - tabs reuse the same R process.
    """
    total = len(session_names)
    timings = []
    for i, session_name in enumerate(session_names):
        launch = launch_session_with_retries(
            page, home_url, session_name, attempts=attempts, label=label
        )
        if set_working_dir:
            set_session_working_directory(page)
        timings.append(launch.elapsed_s)
        print("[rstudio-local] %s: %d/%d launched (%s, %.2fs)"
              % (label, i + 1, total, session_name, launch.elapsed_s))
    return timings


def return_to_session_list(page, home_url, timeout_ms=30000):
    """Sends `page` back from the last launched IDE to the live session list."""
    page.goto(home_url)
    page.get_by_text("New Session", exact=True).first.wait_for(state="visible", timeout=timeout_ms)


def close_tabs(tabs):
    """Closes every tab in `tabs`, ignoring any that are already gone."""
    for tab in tabs:
        try:
            tab.close()
        except Exception:
            pass


def run_command_in_tabs_sequentially(tabs, session_names, command, timeout_ms, label, script_runs=None):
    """Runs `command` in each tab, one at a time, printing each console
    response; returns run times (s) in tab order. Each run is recorded in
    `script_runs` (see common/script_timings.py)."""
    total = len(tabs)
    timings = []
    for i, tab in enumerate(tabs):
        elapsed_s = run_timed_console_command(
            tab, session_names[i], command, timeout_ms, script_runs
        )
        timings.append(elapsed_s)
        print("[rstudio-local] %s: %d/%d ran %r in tab (%s, %.2fs)"
              % (label, i + 1, total, command, session_names[i], elapsed_s))
        print(
            "[rstudio-local] %s: %d/%d console response (%s):\n%s"
            % (label, i + 1, total, session_names[i], get_console_output(tab))
        )
    return timings


def submit_command_in_new_tabs(context, home_url, session_names, command, tabs, label):
    """Opens each session in a new tab and submits `command` without waiting.
    Tabs go into `tabs` immediately so the caller can close them on error.
    Returns one pending-run dict per session for monitor_pending_runs().
    """
    total = len(session_names)
    pending_runs = []
    for i, session_name in enumerate(session_names):
        tab = context.new_page()
        tabs.append(tab)
        open_existing_session(tab, home_url, session_name)
        marker, started = submit_console_command(tab, command)
        pending_runs.append({
            "index": i, "session_name": session_name, "tab": tab,
            "marker": marker, "started": started, "ended": None, "status": "running",
        })
        print(
            "[rstudio-local] %s: %d/%d opened tab and submitted %r (%s) - "
            "not waiting for it to finish"
            % (label, i + 1, total, command, session_name)
        )
    return pending_runs


def monitor_pending_runs(pending_runs, command, timeout_ms, poll_interval_s, label):
    """Polls every running tab until each finishes or passes `timeout_ms`.
    Returns (script_timings, failures) indexed like `pending_runs`; None
    means not finished / no error.
    """
    total = len(pending_runs)
    script_timings = [None] * total
    failures = [None] * total
    pending = list(range(total))
    while pending:
        still_pending = []
        for i in pending:
            run = pending_runs[i]
            timed_out_now = time.time() - run["started"] > timeout_ms / 1000.0
            try:
                done = is_console_command_done(run["tab"], run["marker"])
            except Exception as exc:
                # Console unreachable (e.g. session crashed under load) -
                # record it and keep monitoring the other tabs.
                failures[i] = run["status"] = str(exc)
                print(
                    "[rstudio-local] %s: %d/%d tab errored while checking %r (%s): %s"
                    % (label, i + 1, total, command, run["session_name"], exc)
                )
                continue
            if done:
                run["ended"] = time.time()
                run["status"] = "ok"
                elapsed_s = run["ended"] - run["started"]
                script_timings[i] = elapsed_s
                print(
                    "[rstudio-local] %s: %d/%d finished %r in tab (%s, %.2fs)"
                    % (label, i + 1, total, command, run["session_name"], elapsed_s)
                )
                print(
                    "[rstudio-local] %s: %d/%d console response (%s):\n%s"
                    % (label, i + 1, total, run["session_name"], get_console_output(run["tab"]))
                )
            elif not timed_out_now:
                still_pending.append(i)
            else:
                # Timed out - dropped; reported by unfinished_runs().
                run["status"] = "timed out"
        pending = still_pending
        if pending:
            time.sleep(poll_interval_s)
    return script_timings, failures


def unfinished_runs(pending_runs, script_timings, failures):
    """"<session_name> (<error or 'timed out'>)" for every unfinished run."""
    return [
        "%s (%s)" % (run["session_name"], failures[i] or "timed out")
        for i, run in enumerate(pending_runs)
        if script_timings[i] is None
    ]


def launch_sessions_scenario(page, home_url, before_ids, session_names, attempts, label):
    """Launches every session and prints launch times; always cleans up."""
    try:
        timings = launch_sessions(page, home_url, session_names, attempts, label)

        print(
            "\n[rstudio-local] %s: launched %d sessions; per-session launch time (s): %s"
            % (label, len(session_names), format_timings(timings))
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)


def run_command_in_tabs_scenario(
    page, context, home_url, before_ids, session_names, attempts, command, timeout_ms, label,
    csv_path=None,
):
    """Launches every session, reopens each in a tab and runs `command` in
    each tab one at a time; always closes tabs and cleans up. Writes script
    timings to `csv_path` (default
    evidence/rstudio_script_timings_<label>_sequential_tabs.csv). Returns the
    sessions that failed to reopen (then no command is run), else [].
    """
    tabs = []
    script_runs = []
    try:
        launch_timings = launch_sessions(
            page, home_url, session_names, attempts, label, set_working_dir=True
        )

        tabs, open_results = open_sessions_in_tabs(context, home_url, session_names)
        open_failures = failed(open_results)
        if open_failures:
            return open_failures

        script_timings = run_command_in_tabs_sequentially(
            tabs, session_names, command, timeout_ms, label, script_runs
        )

        print(
            "\n[rstudio-local] %s: launched %d sessions and ran %r in each tab; "
            "launch time (s): %s; script time (s): %s"
            % (label, len(session_names), command, format_timings(launch_timings), format_timings(script_timings))
        )
        return []
    finally:
        write_script_timings_csv(
            csv_path or script_timings_csv_path("%s_sequential_tabs" % label), script_runs
        )
        close_tabs(tabs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


def run_command_concurrently_in_tabs_scenario(
    page, context, home_url, before_ids, session_names, attempts, command, timeout_ms,
    poll_interval_s, label, csv_path=None,
):
    """Launches every session, submits `command` in each one's tab without
    waiting, then monitors all together; always closes tabs and cleans up.
    Writes per-session script timings to `csv_path` (default
    evidence/rstudio_script_timings_<label>_concurrent_tabs.csv).
    Returns unfinished_runs() (errored or timed out), else [].
    """
    tabs = []
    try:
        launch_timings = launch_sessions(
            page, home_url, session_names, attempts, label, set_working_dir=True
        )
        return_to_session_list(page, home_url)

        pending_runs = submit_command_in_new_tabs(
            context, home_url, session_names, command, tabs, label
        )
        script_timings, failures = monitor_pending_runs(
            pending_runs, command, timeout_ms, poll_interval_s, label
        )
        write_script_timings_csv(
            csv_path or script_timings_csv_path("%s_concurrent_tabs" % label), pending_runs
        )

        unfinished = unfinished_runs(pending_runs, script_timings, failures)
        if unfinished:
            return unfinished

        print(
            "\n[rstudio-local] %s: launched %d sessions and ran %r concurrently in each tab; "
            "launch time (s): %s; script time (s): %s"
            % (label, len(session_names), command, format_timings(launch_timings), format_timings(script_timings))
        )
        return []
    finally:
        close_tabs(tabs)
        cleanup_and_verify_sessions(page, home_url, before_ids)


# --- Generic helpers shared across the tests/ suite -------------------------

def optional_int(value):
    """int(value), or None when the env value is unset/empty."""
    return int(value) if value else None


def optional_float(value):
    """float(value), or None when the env value is unset/empty."""
    return float(value) if value else None


def is_headless(request):
    """False only when pytest was run with --headed (for browsers the tests
    open themselves, outside the pytest-playwright fixtures)."""
    return not bool(request.config.getoption("--headed", default=False))


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
    return "launch=%.2fs project=%.2fs text_file=%.2fs" % (
        r["launch_elapsed_s"], r["project_elapsed_s"], r["text_file_elapsed_s"])


def describe_launch_project_text_files(r):
    return "launch=%.2fs project=%.2fs text_files=%s" % (
        r["launch_elapsed_s"], r["project_elapsed_s"], ["%.2fs" % s for s in r["text_file_elapsed_s"]])


def describe_launch_project(r):
    return "launch=%.2fs project=%.2fs" % (r["launch_elapsed_s"], r["project_elapsed_s"])


def describe_open_text_files(r):
    return "open=%.2fs text_files=%s" % (
        r["open_elapsed_s"], ["%.2fs" % s for s in r["text_file_elapsed_s"]])


def print_script_result(label, result, command):
    """Prints one session's script-run result under `label`."""
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
    return sum(values) / len(values)


def capture_server_and_db_metrics(minutes):
    """Best-effort CloudWatch server + DB metrics for the last `minutes`."""
    capture_server_metrics(minutes=minutes)
    capture_db_metrics(minutes=minutes)


def create_sessions_with_working_dir(page, home_url, session_names):
    """Creates each named session in turn and sets its working directory,
    printing the active-session count after each. A failed launch is
    recorded, not raised. Returns (timings, created_names, failures)."""
    total = len(session_names)
    timings, created, failures = [], [], []
    for i, name in enumerate(session_names):
        try:
            launch = launch_session(page, home_url, session_name=name)
            set_session_working_directory(page)
            timings.append(launch.elapsed_s)
            created.append(launch.session_name)
            print_active_session_count(page, home_url, label="launch %d/%d" % (i + 1, total))
        except Exception as exc:
            failures.append((i + 1, str(exc)))
    return timings, created, failures


# --- API-side helpers (test_latency.py, test_volume.py) ---------------------

def wait_for_file_content(reader, project_id, path, content, timeout_s, poll_s=0.2):
    """Polls reader.read_file_api() until `content` appears; returns the
    seconds it took, or None if it never did within `timeout_s`."""
    started = time.time()
    while time.time() - started < timeout_s:
        try:
            if content in reader.read_file_api(project_id, path):
                return time.time() - started
        except Exception:
            pass
        time.sleep(poll_s)
    return None


def time_calls(fn, count):
    """Calls fn() `count` times; returns the sorted durations in seconds."""
    samples = []
    for _ in range(count):
        t0 = time.time()
        fn()
        samples.append(time.time() - t0)
    return sorted(samples)


def p95(sorted_samples):
    return sorted_samples[int(len(sorted_samples) * 0.95) - 1]


def collect_action_breaches(client, project_id, actions, limit_s):
    """Runs each action; returns (action, status, hours) for every one that
    did not complete within `limit_s`."""
    breaches = []
    for action in actions:
        started = client.start_action(project_id, action)
        state, elapsed = client.wait_for_action(started.get("action_id"), timeout_s=limit_s + 600)
        if state.get("status") != "completed" or elapsed > limit_s:
            breaches.append((action, state.get("status"), round(elapsed / 3600, 2)))
    return breaches
