"""Helpers for tests that create sessions through the Workbench API and then
drive them in the browser: create sessions from several CSVs, give groups of
open sessions different workloads (Workbench jobs, repeated console
commands) and clean everything up afterwards.

Run records use the common.script_timings format ("session_name",
"started", "ended", "status").
"""
import os
import posixpath
import threading
import time

from playwright.sync_api import sync_playwright

from common import locators
from common.api_helper import launch_sessions_from_csv, read_session_names, stop_session, write_session_ids
from common.launch_already_created_sessions import (
    CONSOLE_READY_TIMEOUT_MS,
    LaunchedSession,
    close_launched_sessions,
    launch_already_created_sessions,
    run_script_in_launched_sessions,
)
from common.evidence import save_screenshot
from common.rstudio_console_commands import (
    is_console_command_done,
    r_string,
    submit_console_command,
    wait_for_console_ready,
)
from common.rstudio_session_helper import (
    goto_session_list,
    is_session_alive,
    is_session_listed,
    launch_new_session,
    open_existing_session,
    row_ids,
)
from common.rstudio_backgroundjob import (
    BACKGROUND_JOB_FAILED_STATES,
    get_background_job_state,
    start_background_job,
)
from common.rstudio_workbenchjob import get_job_status, start_workbench_job, stop_workbench_job
from common.script_timings import failed_runs, new_run_record
from common.session_actions import (
    close_all_editor_files,
    login_to_posit_workbench,
    recreate_session_folder,
    set_session_working_directory,
)
from common.session_cleanup import cleanup_and_verify_sessions, quit_sessions_named
from common.step_log import current_browser, log_step


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


def launch_or_reopen_session(page, home_url, session_name, attempts=3, launch_timeout_ms=180000,
                             retry_wait_s=15):
    """Launch `session_name` from the New Session dialog and wait for its
    IDE and console, with up to `attempts` tries `retry_wait_s` apart.

    A launch that times out may still have created the session (it can just
    be slow to start), so each retry first looks for `session_name` on the
    session list and opens that session instead of launching a duplicate;
    only if it is not listed is a new one launched. Returns (SessionLaunch,
    console ready seconds); raises the last error if every attempt fails.
    """
    def _attempt(attempt):
        if attempt > 1 and is_session_listed(page, home_url, session_name):
            print("\n[rstudio-local] session %s already exists, opening it instead of launching a new one"
                  % session_name)
            launch = open_existing_session(page, home_url, session_name, timeout_ms=launch_timeout_ms)
        else:
            launch = launch_new_session(page, home_url, session_name=session_name, timeout_ms=launch_timeout_ms)
        if not is_session_alive(page):
            raise AssertionError("session %s is not active after launch" % session_name)
        ready_s = wait_for_console_ready(page, timeout_ms=CONSOLE_READY_TIMEOUT_MS)
        close_all_editor_files(page, session_name)
        return launch, ready_s

    for attempt in range(1, attempts + 1):
        try:
            return _attempt(attempt)
        except Exception as exc:
            print("\n[rstudio-local] session %s attempt %d/%d failed: %s" % (session_name, attempt, attempts, exc))
            if attempt == attempts:
                raise
            time.sleep(retry_wait_s)


def create_sessions_in_ui_from_csvs(context, user, csv_paths, attempts=3, launch_timeout_ms=180000,
                                    retry_wait_s=15):
    """Log in as `user` and launch one session per name in each CSV through
    the New Session dialog, each in its own tab, leaving the tab on the
    session's IDE with its console ready. Sessions already listed under one
    of the CSV names (e.g. left over from an earlier run) are quit first, so
    every session is created afresh. Failed launches are retried with
    launch_or_reopen_session(), which reopens a session the failed attempt
    created rather than launching a duplicate.

    The login (session list) tab is closed before the first launch: every
    IDE tab holds a long-poll connection to the server, and with 5 IDE tabs
    plus the session list a browser reaches Chrome's limit of 6 HTTP/1.1
    connections per host, so further requests queue and tabs stall. The
    context stays signed in; quit_ui_created_sessions() opens a new tab.

    Returns (home_url, before_ids, names_by_csv, launched, failed):
    before_ids are the session rows listed before any launch (for
    quit_ui_created_sessions()),
    names_by_csv maps each CSV path to the names launched from it, launched
    holds a LaunchedSession per launched name and failed "<name>: <error>"
    for the rest. A failed launch does not stop the others.
    """
    return create_sessions_in_ui_across_browsers(
        [context], user, csv_paths, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
        retry_wait_s=retry_wait_s,
    )


def browser_name_prefixes(browser_count):
    """The session name prefix of each browser: "B1_", "B2_", ..."""
    return ["B%d_" % (i + 1) for i in range(browser_count)]


def run_in_parallel_browsers(browser_name, launch_args, context_args, prefixes, worker, barriers=(),
                             stagger_s=0, join_timeout_s=None, debug_ports=None):
    """Open one browser per entry in `prefixes`, each in its own thread with
    its own Playwright (the sync API cannot share one between threads), and
    call worker(context, prefix) there with a new context of that browser.
    Browsers are started `stagger_s` apart (browser n waits n * stagger_s),
    so their logins do not collide.
    Returns [(prefix, result, error)] in `prefixes` order: result is what
    worker returned, error the exception that escaped it (else None).

    If a thread fails outside worker - or worker raises - each of `barriers`
    is aborted, so the other threads waiting on them are not stuck.

    With join_timeout_s, stops waiting for the threads after that long: a
    thread still running (e.g. frozen inside a Playwright call) is reported
    as a TimeoutError and left behind (daemon thread, ended with the
    process), so one stuck browser cannot hang the test for ever.

    With debug_ports ({prefix: port}, Chromium only), each browser listens
    for DevTools on 127.0.0.1:<port>, so another thread can connect to it
    and screenshot its tabs while the browser's own thread is stuck.
    """
    results = [None] * len(prefixes)

    def _run(index, prefix):
        try:
            if index and stagger_s:
                print("\n[rstudio-local] browser %s: starting in %ds" % (prefix, index * stagger_s))
                time.sleep(index * stagger_s)
            args = dict(launch_args)
            if debug_ports and prefix in debug_ports and browser_name == "chromium":
                args["args"] = list(args.get("args") or []) + ["--remote-debugging-port=%d" % debug_ports[prefix]]
            with sync_playwright() as playwright:
                browser = getattr(playwright, browser_name).launch(**args)
                log_step("Browser %s started (%s)", prefix.rstrip("_"), browser_name)
                try:
                    context = browser.new_context(**{**context_args, "ignore_https_errors": True})
                    results[index] = (prefix, worker(context, prefix), None)
                finally:
                    browser.close()
                    log_step("Browser %s closed", prefix.rstrip("_"))
        except BaseException as exc:
            print("\n[rstudio-local] browser %s failed: %s" % (prefix, exc))
            results[index] = (prefix, None, exc)
            for barrier in barriers:
                barrier.abort()

    threads = [
        threading.Thread(target=_run, args=(i, prefix), name="browser-%s" % prefix.rstrip("_"),
                         daemon=join_timeout_s is not None)
        for i, prefix in enumerate(prefixes)
    ]
    for thread in threads:
        thread.start()
    deadline = time.time() + join_timeout_s if join_timeout_s is not None else None
    for i, thread in enumerate(threads):
        thread.join(None if deadline is None else max(0, deadline - time.time()))
        if thread.is_alive():
            print("\n[rstudio-local] browser %s still running after %ds - not waiting for it any longer"
                  % (prefixes[i], join_timeout_s))
            results[i] = (prefixes[i], None, TimeoutError(
                "browser did not finish within %ds (stuck - its sessions may need deleting by hand)"
                % join_timeout_s))
    return results


def prefixed_session_names(csv_paths, prefixes=("",)):
    """Every name in `csv_paths` with each of `prefixes` in front."""
    return [prefix + name for prefix in prefixes for csv_path in csv_paths for name in read_session_names(csv_path)]


def create_sessions_in_ui_across_browsers(contexts, user, csv_paths, prefixes=("",), attempts=3,
                                          launch_timeout_ms=180000, retry_wait_s=15):
    """create_sessions_in_ui_from_csvs() in several browser contexts (e.g.
    one per browser): `user` logs in in each, and each context launches
    every CSV name, each in its own tab, prefixed with its entry in
    `prefixes` (one per context) so the names do not clash - with
    browser_name_prefixes(2), TEST_USER_1 is launched as B1_TEST_USER_1 in
    contexts[0] and as B2_TEST_USER_1 in contexts[1].

    Returns the same tuple as create_sessions_in_ui_from_csvs(), with the
    prefixed names. Every context's login tab is closed before the first
    launch (see create_sessions_in_ui_from_csvs()).
    """
    assert len(prefixes) == len(contexts), "need one name prefix per browser context"
    names_in_csv = {csv_path: read_session_names(csv_path) for csv_path in csv_paths}
    home_url, before_ids = login_and_quit_sessions_named(
        contexts, user, prefixed_session_names(csv_paths, prefixes),
    )

    names_by_csv = {csv_path: [] for csv_path in names_in_csv}
    launched, failed = [], []
    for browser_index, (context, prefix) in enumerate(zip(contexts, prefixes)):
        where = " in browser %d of %d" % (browser_index + 1, len(contexts)) if len(contexts) > 1 else ""
        for csv_path, names in names_in_csv.items():
            csv_launched, csv_failed = launch_sessions_in_tabs(
                context, home_url, [prefix + n for n in names], attempts=attempts,
                launch_timeout_ms=launch_timeout_ms, retry_wait_s=retry_wait_s, where=where,
            )
            launched += csv_launched
            failed += csv_failed
            names_by_csv[csv_path] += [s.session_name for s in csv_launched]
    return home_url, before_ids, names_by_csv, launched, failed


def login_and_quit_sessions_named(contexts, user, session_names):
    """Log in as `user` in each of `contexts`, quit every listed session
    named one of `session_names` (e.g. left over from an earlier run) and
    note the rows still listed. The login tabs are then closed (see
    create_sessions_in_ui_from_csvs()); the contexts stay signed in.

    Returns (home_url, before_ids) for launch_sessions_in_tabs() and
    quit_ui_sessions_named().
    """
    home_pages = [context.new_page() for context in contexts]
    home_url = None
    for home in home_pages:
        home_url = login_to_posit_workbench(home, user=user)
    home_page = home_pages[0]
    quit_names = quit_sessions_named(home_page, home_url, session_names)
    if quit_names:
        log_step("Quit %d leftover session(s) from an earlier run: %s", len(quit_names), ", ".join(quit_names))
    else:
        log_step("No leftover sessions from an earlier run to quit")
    goto_session_list(home_page, home_url)
    before_ids = row_ids(home_page)
    for home in home_pages:
        home.close()
    return home_url, before_ids


def launch_sessions_in_tabs(context, home_url, session_names, attempts=3, launch_timeout_ms=180000,
                            retry_wait_s=15, where=""):
    """Launch each of `session_names` from the New Session dialog in its own
    tab of the signed-in `context`, one after another, with
    launch_or_reopen_session(); `where` is added to the log lines (e.g.
    " in browser 1 of 2") unless they come from a browser thread, which
    log_step() tags itself. A failed launch does not stop the others.

    Returns (launched, failed): a LaunchedSession per launched name, its tab
    left on the IDE with the console ready, and "<name>: <error>" for the rest.
    """
    where = "" if current_browser() else where
    launched, failed = [], []
    for i, name in enumerate(session_names, 1):
        log_step("Creating RStudio session %s (%d of %d)%s", name, i, len(session_names), where)
        page = context.new_page()
        try:
            launch, ready_s = launch_or_reopen_session(
                page, home_url, name, attempts=attempts, launch_timeout_ms=launch_timeout_ms,
                retry_wait_s=retry_wait_s,
            )
        except Exception as exc:
            save_screenshot(page, "launch_failed_%s.png" % name, always=True)
            page.close()
            failed.append("%s: %s" % (name, exc))
            log_step("RStudio session %s could not be created%s: %s", name, where, exc)
            continue
        log_step("RStudio session %s created successfully%s: IDE opened in %.2fs, console ready %.2fs later",
                 name, where, launch.elapsed_s, ready_s)
        launched.append(LaunchedSession(session_name=name, page=page, launch=launch, error=None))
    return launched, failed


def quit_ui_created_sessions(context, home_url, before_ids, csv_paths, prefixes=("",)):
    """Quit, from the session list in a new tab of the signed-in `context`,
    the sessions this test created: rows named in one of `csv_paths` (with
    one of `prefixes` in front) that were not listed in `before_ids` (see
    create_sessions_in_ui_from_csvs()). Other sessions - including ones
    other people or the test's own Workbench jobs start meanwhile - are
    left alone. Close the IDE tabs first, so this tab gets a connection.
    Errors are printed, not raised.
    """
    quit_ui_sessions_named(
        context, home_url, before_ids, prefixed_session_names(csv_paths, prefixes),
        "named in %s" % ", ".join(os.path.basename(p) for p in csv_paths),
    )


def quit_ui_sessions_named(context, home_url, before_ids, session_names, what=""):
    """quit_ui_created_sessions() for an explicit list of `session_names`;
    `what` describes them in the log line. Errors are printed, not raised."""
    page = None
    try:
        page = context.new_page()
        quit_ids = cleanup_and_verify_sessions(page, home_url, before_ids, session_names=session_names)
        log_step("Quit %d RStudio session(s) created by this test%s", len(quit_ids), " (%s)" % what if what else "")
    except Exception as exc:
        log_step("Could not quit the RStudio sessions created by this test: %s", exc)
    finally:
        if page is not None:
            page.close()


def wait_until_epoch(start_at_epoch, who="this machine"):
    """Sleep until `start_at_epoch` (Unix time, number or string), logging
    the start time for `who`; no wait if it is blank or past."""
    if not start_at_epoch:
        return
    start_at = float(start_at_epoch)
    wait_s = start_at - time.time()
    print("\n[rstudio-local] %s: starting at %s (in %.0fs)"
          % (who, time.strftime("%H:%M:%S", time.localtime(start_at)), max(0.0, wait_s)))
    if wait_s > 0:
        time.sleep(wait_s)


def run_group_workloads_across_browsers(browser_name, launch_args, context_args, user, group_csvs, prefixes,
                                        label, stagger_s=0, attempts=3, launch_timeout_ms=180000,
                                        retry_wait_s=15, **workload):
    """run_group_workloads() in one browser per entry in `prefixes`, run by
    run_in_parallel_browsers(). Each browser logs in as `user` and launches
    every name in `group_csvs` ({"test"|"a"|"b"|"c": csv path}) with its
    prefix in front. `workload` is run_group_workloads()'s arguments after
    `label`.

    Browsers wait for each other before the workloads (skipped everywhere
    if any browser failed to launch) and before cleanup; each closes its
    tabs and quits the sessions it created even on failure.

    Returns (runs, errors): all run records, and "browser <prefix>: <error>"
    per failed browser.
    """
    ready = threading.Barrier(len(prefixes))
    done = threading.Barrier(len(prefixes))
    launch_failed = set()

    def _worker(context, prefix):
        return _run_group_workloads_in_browser(
            context, prefix, user, group_csvs, "%s %s" % (label, prefix), ready, done, launch_failed,
            attempts, launch_timeout_ms, retry_wait_s, workload,
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
            errors.append("browser %s: %s" % (prefix, error))
    return runs, errors


def _run_group_workloads_in_browser(context, prefix, user, group_csvs, label, ready, done, launch_failed,
                                    attempts, launch_timeout_ms, retry_wait_s, workload):
    """One browser's part of run_group_workloads_across_browsers(); a
    browser whose launch fails adds its prefix to `launch_failed`. Returns
    (runs, error): its run records and the error that failed it, else None."""
    home_url = before_ids = None
    launched, runs, groups, error = [], [], {}, None
    try:
        try:
            home_url, before_ids, names_by_csv, launched, failed = create_sessions_in_ui_across_browsers(
                [context], user, group_csvs.values(), prefixes=[prefix],
                attempts=attempts, launch_timeout_ms=launch_timeout_ms, retry_wait_s=retry_wait_s,
            )
            assert not failed, "sessions that failed to launch: %s" % failed
            empty = [path for path, names in names_by_csv.items() if not names]
            assert not empty, "no session names found in %s" % empty
            by_name = {s.session_name: s for s in launched}
            groups = {g: [by_name[name] for name in names_by_csv[path]] for g, path in group_csvs.items()}
        except Exception:
            launch_failed.add(prefix)
            raise
        finally:
            print("\n[rstudio-local] browser %s: sessions launched, waiting for the other browsers" % prefix)
            ready.wait()
        if launch_failed:
            raise AssertionError("workloads skipped: launch failed in browser(s) %s" % sorted(launch_failed))
        run_group_workloads(groups, runs, label, **workload)
    except Exception as exc:
        error = exc
    finally:
        try:
            done.wait()
        except threading.BrokenBarrierError:
            pass
        # Background jobs end when their sessions are quit below.
        stop_jobs_and_close_tabs(launched)
        if home_url is not None:
            quit_ui_created_sessions(context, home_url, before_ids, group_csvs.values(), [prefix])
    return runs, error


def run_group_workloads(groups, runs, label, duration_s, script_path, output_dir, output_file_name,
                        job_script_path, list_files_interval_s=30, background_jobs=False):
    """Run each group's workload at once until `duration_s` from now and
    assert none failed. `groups` maps "test", "a", "b" and "c" to
    LaunchedSession lists:

    - test: source `script_path` (must leave `output_file_name` in
            `output_dir`/<session name>)
    - a:    list.files(`output_dir`), repeated list_files_interval_s after
            each answer
    - b:    `job_script_path` as a Workbench job, or with background_jobs
            as an RStudio background job (must not fail)
    - c:    nothing - left idle

    Run records are added to `runs` as they come in, so the caller can save
    them even on failure; `label` tags the summary line.

    Flow: start jobs (b) -> run script (test) while polling list.files (a)
    -> keep going until duration_s -> check jobs
    """
    started = time.time()
    deadline = started + duration_s

    # Jobs run on their own once started; list.files() runs between the
    # script polls and then until the deadline.
    start_jobs, check_jobs = (
        (start_background_jobs, check_background_jobs) if background_jobs
        else (start_workbench_jobs, check_workbench_jobs)
    )
    job_runs, jobs = start_jobs(groups["b"], job_script_path, output_dir)
    runs += job_runs
    list_files = ListFilesLoop(groups["a"], output_dir, deadline, list_files_interval_s)
    runs += run_script_in_launched_sessions(
        groups["test"], script_path, working_dir=output_dir, timeout_ms=ms_left(deadline),
        output_path=output_file_name, per_session_dir=True, on_poll=list_files.tick,
    )
    runs += list_files.run_until_deadline()
    check_jobs(jobs)

    failures = report_failed_runs(runs, label, started, idle_sessions=groups["c"])
    assert not failures, "runs that did not finish: %s" % failures


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
    stop_jobs_and_close_tabs(launched, job_sessions, job_script_path)
    quit_sessions(created, user)


def stop_jobs_and_close_tabs(launched, job_sessions=(), job_script_path=None):
    """The browser half of clean_up(): stop the user's running
    `job_script_path` Workbench jobs and close the tabs in `launched`."""
    job_pages = [s.page for s in job_sessions if s.page is not None]
    if job_pages and job_script_path:
        stop_workbench_jobs(job_pages[0], posixpath.basename(job_script_path))
    close_launched_sessions(launched)


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
    """setwd each session to `output_dir`/<session name> (emptied first, see
    recreate_session_folder())
    and start `script_path` as a Workbench job. Returns (records, started)
    where started holds (page, job_name, record) for each job that started.
    """
    records, started = [], []
    for session in sessions:
        record = new_run_record(session.session_name)
        records.append(record)
        try:
            session_dir = recreate_session_folder(session.page, output_dir, session.session_name)
            set_session_working_directory(session.page, path=session_dir)
            record["started"] = time.time()
            job_name, _ = start_workbench_job(session.page, script_path)
            started.append((session.page, job_name, record))
            print("\n[rstudio-local] session %s: started Workbench job %s" % (session.session_name, job_name))
            save_screenshot(session.page, "workbench_job_started_%s.png" % session.session_name)
        except Exception as exc:
            record["status"] = "job start failed: %s" % exc
            save_screenshot(session.page, "job_start_failed_%s.png" % session.session_name, always=True)
    return records, started


def start_background_jobs(sessions, script_path, output_dir):
    """start_workbench_jobs(), but start `script_path` as an RStudio
    background job running in `output_dir`/<session name> (emptied first).
    Returns (records, started) where started holds (page, job_id, record)
    for each job that started. The jobs end when their session is quit.
    """
    records, started = [], []
    for session in sessions:
        record = new_run_record(session.session_name)
        records.append(record)
        try:
            session_dir = recreate_session_folder(session.page, output_dir, session.session_name)
            record["started"] = time.time()
            job_id = start_background_job(session.page, script_path, session_dir)
            started.append((session.page, job_id, record))
            print("\n[rstudio-local] session %s: started background job %s (%s)"
                  % (session.session_name, posixpath.basename(script_path), job_id))
            save_screenshot(session.page, "background_job_started_%s.png" % session.session_name)
        except Exception as exc:
            record["status"] = "background job start failed: %s" % exc
            save_screenshot(session.page, "background_job_start_failed_%s.png" % session.session_name, always=True)
    return records, started


def check_background_jobs(started):
    """Mark each job from start_background_jobs() "ok" if it is still
    running or has succeeded; failed, cancelled, not listed or unreadable
    ("unknown") states fail the run.
    """
    for page, job_id, record in started:
        try:
            state = get_background_job_state(page, job_id)
        except Exception as exc:
            record["status"] = "background job state unknown: %s" % exc
            continue
        record["ended"] = time.time()
        failed = state in BACKGROUND_JOB_FAILED_STATES or state in ("not listed", "unknown")
        record["status"] = "background job %s" % state if failed else "ok"
        print(
            "\n[rstudio-local] session %s: background job %s is %r after %.2fs"
            % (record["session_name"], job_id, state, record["ended"] - record["started"])
        )
        save_screenshot(page, "background_job_status_%s.png" % record["session_name"])


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
        save_screenshot(page, "workbench_job_status_%s.png" % record["session_name"])


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
        # Sessions whose first call / first answer has been screenshotted
        self.captured = set()
        self.output_captured = set()

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
                    if session not in self.captured:
                        # Only the first call: the command repeats every interval_s.
                        self.captured.add(session)
                        save_screenshot(session.page, "console_input_%s.png" % session.session_name)
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
                if session not in self.output_captured:
                    self.output_captured.add(session)
                    save_screenshot(session.page, "console_output_%s.png" % session.session_name)
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
