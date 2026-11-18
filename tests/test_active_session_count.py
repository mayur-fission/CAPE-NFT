"""Monitor how many Workbench sessions are running: every
ACTIVE_SESSIONS_POLL_INTERVAL_S (10 s), count the rows on each user's
session list with status Active (X) and Executing (Y), and record them with
their total X + Y (the running sessions). Creates and quits no sessions.

How long it runs: at most ACTIVE_SESSIONS_MONITOR_S (20 min). It stops
sooner, after one last poll, once ACTIVE_SESSIONS_STOP_FILE appears (default
evidence/.active_sessions_stop; Jenkins writes it when every other test of the
build has finished, or create it yourself to stop the count).

Stopping it by hand without losing evidence or failing the test - any of:
- create the stop file (e.g. `touch evidence/.active_sessions_stop` in the
  repo / Jenkins workspace): one last poll, then it ends;
- Ctrl+C once (or SIGTERM): the poll in progress finishes, then it ends.
Either way the CSV is written and attached and the test passes. A second
Ctrl+C stops it at once (the CSV still holds every finished poll).

With ACTIVE_SESSIONS_READY_FILE set (Jenkins, with other tests alongside), that
file is written once every user is logged in; Jenkins holds the other tests
back until then, so counting starts before their browsers log in and launch.

The Workbench home page only lists the signed-in user's own sessions (the
admin dashboard is not enabled), so the total is over the users in
ACTIVE_SESSIONS_USERS (e.g. "1,2"), not the whole server.
"""
import csv
import os
import signal
import time
from datetime import datetime

import pytest

from config.config import REPO_ROOT, env, env_list, evidence_dir
from common.evidence import save_screenshot
from common.rstudio_session_helper import goto_session_list, stable_session_status_counts
from common.session_actions import login_to_posit_workbench
from common.perf_report import attach_allure_file

pytestmark = pytest.mark.rstudio_local

USERS = [int(u) for u in env_list("ACTIVE_SESSIONS_USERS", ["1"])]
POLL_INTERVAL_S = float(env("ACTIVE_SESSIONS_POLL_INTERVAL_S", "10"))
MONITOR_S = float(env("ACTIVE_SESSIONS_MONITOR_S", "1200"))
# Relative paths are under the repo root (where Jenkins writes it). Always
# watched, so a run can be stopped by hand by creating it.
STOP_FILE = env("ACTIVE_SESSIONS_STOP_FILE") or "evidence/.active_sessions_stop"
if not os.path.isabs(STOP_FILE):
    STOP_FILE = os.path.join(REPO_ROOT, STOP_FILE)
# Set by Ctrl+C / SIGTERM: end after the poll in progress.
_stop_signal = []
READY_FILE = env("ACTIVE_SESSIONS_READY_FILE") or None
if READY_FILE and not os.path.isabs(READY_FILE):
    READY_FILE = os.path.join(REPO_ROOT, READY_FILE)
CSV_NAME = "active_sessions.csv"  # in the test's evidence folder


def _csv_path():
    return os.path.join(evidence_dir(), CSV_NAME)
CSV_HEADER = ["Time", "Elapsed (s)", "Poll", "User", "Active", "Executing", "Total Sessions (Active + Executing)",
              "Listed Sessions", "Statuses", "Error"]


TOTAL_CSV_NAME = "total_active_sessions.csv"
USER_CSV_NAME = "user%d_active_sessions.csv"
USER_CSV_HEADER = ["Time", "Elapsed (s)", "Poll", "Active", "Executing", "Total Sessions (Active + Executing)",
                   "Change vs Previous Poll", "Session Drop", "Listed Sessions", "Statuses", "Error"]
TOTAL_CSV_HEADER = ["Time", "Elapsed (s)", "Poll", "Active", "Executing", "Total Sessions (Active + Executing)",
                    "Change vs Previous Poll", "Session Drop", "Users Read", "Note"]


def _write_csv(rows, path=None, header=CSV_HEADER):
    """(Re)write a CSV with `header` and `rows` (default: the combined counts
    CSV), so the counts so far are on disk even if the run is aborted."""
    with open(path or _csv_path(), "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def _change(total, previous):
    """(change, drop) columns: change vs the previous readable poll, and
    "DROP (-n)" when the count went down."""
    if previous is None:
        return "", ""
    diff = total - previous
    return "%+d" % diff, "DROP (%d)" % diff if diff < 0 else ""


def _split_rows(rows):
    """From the combined rows ([time, elapsed, poll, "user N", active,
    executing, total, listed, statuses, error]) build the rows of each user's
    CSV and of the totals CSV, with the change vs the previous poll and a
    drop flag. A poll where a user could not be read gets no total (it would
    look like a drop) and does not move the baseline."""
    per_user, totals, drops = {u: [] for u in USERS}, [], []
    last_user, last_total = {}, None
    for poll in sorted({r[2] for r in rows}):
        poll_rows = [r for r in rows if r[2] == poll and r[3] != "total"]
        stamp, elapsed = poll_rows[0][0], poll_rows[0][1]
        unread = []
        for r in poll_rows:
            user = int(r[3].split()[1])
            if r[9]:  # error
                unread.append(user)
                per_user[user].append([r[0], r[1], poll, "", "", "", "", "", "", "", r[9]])
                continue
            change, drop = _change(r[6], last_user.get(user))
            last_user[user] = r[6]
            per_user[user].append([r[0], r[1], poll, r[4], r[5], r[6], change, drop, r[7], r[8], ""])
        if unread:
            totals.append([stamp, elapsed, poll, "", "", "", "", "", len(poll_rows) - len(unread),
                           "incomplete - user(s) %s could not be read" % ", ".join(map(str, unread))])
            continue
        active = sum(r[4] for r in poll_rows)
        executing = sum(r[5] for r in poll_rows)
        total = active + executing
        change, drop = _change(total, last_total)
        if drop:
            drops.append((poll, stamp, last_total, total))
        last_total = total
        totals.append([stamp, elapsed, poll, active, executing, total, change, drop, len(poll_rows), ""])
    return per_user, totals, drops


def _write_split_csvs(rows):
    """Write user<N>_active_sessions.csv per user and total_active_sessions.csv;
    return (paths, totals rows, drops)."""
    per_user, totals, drops = _split_rows(rows)
    paths = []
    for user, user_rows in per_user.items():
        path = os.path.join(evidence_dir(), USER_CSV_NAME % user)
        _write_csv(user_rows, path, USER_CSV_HEADER)
        paths.append(path)
    path = os.path.join(evidence_dir(), TOTAL_CSV_NAME)
    _write_csv(totals, path, TOTAL_CSV_HEADER)
    paths.append(path)
    return paths, totals, drops


def _summary(totals, drops):
    """One-paragraph summary of the totals: peak, lowest, last, and every drop."""
    counts = [r[5] for r in totals if r[5] != ""]
    if not counts:
        return "no complete poll - no total recorded"
    text = ("total sessions (Active + Executing): first %d, peak %d, lowest %d, last %d over %d complete poll(s); "
            % (counts[0], max(counts), min(counts), counts[-1], len(counts)))
    if not drops:
        return text + "no session drops"
    return text + "%d drop(s): %s" % (len(drops), "; ".join(
        "poll %d at %s: %d -> %d" % (poll, stamp, before, after) for poll, stamp, before, after in drops))


def _on_stop_signal(signum, frame):
    """First Ctrl+C / SIGTERM: ask the loop to end after the poll in progress.
    A second one gets the default behaviour (stop at once)."""
    _stop_signal.append(signum)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    print("\n[active-sessions] stop requested - finishing the current poll and saving the CSV "
          "(press Ctrl+C again to abort at once)", flush=True)


def _install_stop_handlers():
    """Route SIGINT/SIGTERM to _on_stop_signal; return the old handlers."""
    old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    for s in old:
        signal.signal(s, _on_stop_signal)
    return old


def _wait_until(deadline):
    """Sleep until `deadline`, waking early when a stop is requested (signal
    or stop file)."""
    while time.time() < deadline and not _stop_signal and not os.path.exists(STOP_FILE):
        time.sleep(min(0.5, max(0.0, deadline - time.time())))


def _live(capsys, message):
    """Print `message` past pytest's output capture, so it shows in the
    console (the Jenkins Console Output) while the test is still running."""
    with capsys.disabled():
        print("\n[active-sessions] %s" % message, flush=True)


def test_active_session_count_every_10_seconds(browser, browser_context_args, capsys):
    """Every poll reads every user's session list.

    - In progress: each poll prints a line to the console, e.g.
      "[active-sessions] poll 7 at 2026-10-09 16:20:31 (+60s): Active=9,
      Executing=3, Total sessions=12 (14 listed, users 1)".
    - When over: evidence/active_sessions.csv has one row per poll and
      user (plus a total row with several users) - time, seconds since the
      first poll, the Active, Executing and total (Active + Executing)
      counts, the listed count, every status seen, and the error for a poll
      that could not read the list. It is rewritten after every poll and
      attached to Allure at the end, with a screenshot of each poll.
    - When stopped or completed, also: user<N>_active_sessions.csv per user
      and total_active_sessions.csv (Active, Executing and their total summed
      over the users, per poll), each with the change vs the previous poll
      and "DROP (-n)" where the count went down; a summary (peak, lowest,
      every drop) is printed to the console. Drops are only reported, by
      design: they never fail the test.

    Flow: login as each user (own browser context) -> every POLL_INTERVAL_S
    until MONITOR_S: reload each session list and count Active and Executing
    rows -> write CSV -> close contexts
    """
    contexts, pages, rows, errors = [], {}, [], []
    # A stop file left from an earlier run would end this one at once.
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    del _stop_signal[:]
    old_handlers = _install_stop_handlers()
    try:
        for user in USERS:
            context = browser.new_context(**{**browser_context_args, "ignore_https_errors": True})
            contexts.append(context)
            page = context.new_page()
            pages[user] = (page, login_to_posit_workbench(page, user=user))
        if READY_FILE:
            os.makedirs(os.path.dirname(READY_FILE), exist_ok=True)
            with open(READY_FILE, "w") as fh:
                fh.write("logged in\n")
            _live(capsys, "logged in - the other tests can start")
        _live(capsys, "counting Active + Executing sessions of users %s every %.0fs for at most %.0fs, into %s. "
                      "To stop early (test still passes, CSV kept): create %s or press Ctrl+C once"
              % (", ".join(str(u) for u in USERS), POLL_INTERVAL_S, MONITOR_S, _csv_path(), STOP_FILE))

        started = time.time()
        poll = 0
        while True:
            poll += 1
            poll_started = time.time()
            # Checked before the poll, so the poll after the other tests end is the last one.
            others_done = os.path.exists(STOP_FILE)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elapsed = "%.0f" % (poll_started - started)
            sum_active = sum_executing = sum_listed = failed = 0
            for user, (page, home_url) in pages.items():
                try:
                    goto_session_list(page, home_url)
                    statuses = stable_session_status_counts(page)
                except Exception as exc:
                    if _stop_signal:
                        # Ctrl+C also reaches the browser (on Windows it kills it): a poll
                        # broken by the stop is not a failure - keep the finished polls.
                        break
                    failed += 1
                    errors.append("poll %d, user %d: %s" % (poll, user, exc))
                    rows.append([stamp, elapsed, poll, "user %d" % user, "", "", "", "", "", "error: %s" % exc])
                    _live(capsys, "poll %d at %s: could not read user %d's session list: %s" % (poll, stamp, user, exc))
                    save_screenshot(page, "active_sessions_failed_U%d_poll%d.png" % (user, poll), always=True)
                    continue
                active, executing = statuses.get("Active", 0), statuses.get("Executing", 0)
                listed = sum(statuses.values())
                sum_active += active
                sum_executing += executing
                sum_listed += listed
                rows.append([stamp, elapsed, poll, "user %d" % user, active, executing, active + executing, listed,
                             "; ".join("%s=%d" % kv for kv in sorted(statuses.items())), ""])
                save_screenshot(page, "active_sessions_U%d_poll%d.png" % (user, poll))
            if _stop_signal and len({r[3] for r in rows if r[2] == poll}) < len(pages):
                # The stop cut this poll short: drop its partial rows, keep the finished polls.
                rows[:] = [r for r in rows if r[2] != poll]
                _live(capsys, "stopped by signal - ending after %d full poll(s)" % (poll - 1))
                break
            if len(pages) > 1:
                rows.append([stamp, elapsed, poll, "total", sum_active, sum_executing, sum_active + sum_executing,
                             sum_listed, "", "%d user(s) could not be read" % failed if failed else ""])
            _write_csv(rows)
            _live(capsys, "poll %d at %s (+%ss): Active=%d, Executing=%d, Total sessions=%d (%d listed, users %s)%s"
                  % (poll, stamp, elapsed, sum_active, sum_executing, sum_active + sum_executing, sum_listed,
                     ", ".join(str(u) for u in USERS),
                     " - %d user(s) could not be read" % failed if failed else ""))

            # Polls start POLL_INTERVAL_S apart (a poll that takes longer is
            # followed by the next one straight away).
            next_poll = poll_started + POLL_INTERVAL_S
            if _stop_signal:
                _live(capsys, "stopped by signal - ending after %d poll(s)" % poll)
                break
            if others_done:
                _live(capsys, "stop file %s found - ending after %d poll(s)" % (STOP_FILE, poll))
                break
            if next_poll - started > MONITOR_S:
                _live(capsys, "reached the ACTIVE_SESSIONS_MONITOR_S limit (%.0fs)" % MONITOR_S)
                break
            _wait_until(next_poll)
    finally:
        for s, handler in old_handlers.items():
            signal.signal(s, handler)
        if rows:
            _write_csv(rows)
            attach_allure_file(_csv_path())
            _live(capsys, "%d poll(s) written to %s" % (len({r[2] for r in rows}), _csv_path()))
            try:
                split_paths, totals, drops = _write_split_csvs(rows)
                for path in split_paths:
                    attach_allure_file(path)
                _live(capsys, "per-user and total CSVs: %s" % ", ".join(split_paths))
                _live(capsys, _summary(totals, drops))
            except Exception as exc:  # the combined CSV above is already saved
                _live(capsys, "could not write the per-user / total CSVs: %s" % exc)
        for context in contexts:
            try:
                context.close()
            except Exception:
                if not _stop_signal:
                    raise  # after Ctrl+C the browser may already be gone

    assert not errors, "polls that could not read a session list: %s" % errors
