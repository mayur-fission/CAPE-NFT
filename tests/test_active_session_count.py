"""Monitor how many Workbench sessions are Active: every
ACTIVE_SESSIONS_POLL_INTERVAL_S (10 s) for ACTIVE_SESSIONS_MONITOR_S, count
the Active rows on each user's session list and record the counts. Run it
alongside the load tests (Jenkins RUN_IN_PARALLEL) to see the session count
over the run. Creates and quits no sessions.

The Workbench home page only lists the signed-in user's own sessions (the
admin dashboard is not enabled), so the total is over the users in
ACTIVE_SESSIONS_USERS (e.g. "1,2"), not the whole server.
"""
import csv
import os
import time
from datetime import datetime

import pytest

from common.config import EVIDENCE_DIR, env, env_list
from common.evidence import save_screenshot
from common.rstudio_session_helper import goto_session_list, session_status_counts
from common.session_actions import login_to_posit_workbench
from config.perf_report import attach_allure_file

pytestmark = pytest.mark.rstudio_local

USERS = [int(u) for u in env_list("ACTIVE_SESSIONS_USERS", ["1"])]
POLL_INTERVAL_S = float(env("ACTIVE_SESSIONS_POLL_INTERVAL_S", "10"))
MONITOR_S = float(env("ACTIVE_SESSIONS_MONITOR_S", "300"))
CSV_PATH = os.path.join(EVIDENCE_DIR, "active_sessions.csv")
CSV_HEADER = ["Time", "Elapsed (s)", "Poll", "User", "Active Sessions", "Listed Sessions", "Statuses", "Error"]


def _write_csv(rows):
    """(Re)write CSV_PATH with CSV_HEADER and `rows`, so the counts so far
    are on disk even if the run is aborted."""
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def _live(capsys, message):
    """Print `message` past pytest's output capture, so it shows in the
    console (the Jenkins Console Output) while the test is still running."""
    with capsys.disabled():
        print("\n[active-sessions] %s" % message, flush=True)


def test_active_session_count_every_10_seconds(browser, browser_context_args, capsys):
    """Every poll reads every user's session list.

    - In progress: each poll prints a line to the console, e.g.
      "[active-sessions] poll 7 at 2026-10-09 16:20:31 (+60s): 12 active
      session(s) of 14 listed (users 1)".
    - When over: evidence/active_sessions.csv has one row per poll and
      user (plus a total row with several users) - time, seconds since the
      first poll, Active and listed counts, every status seen, and the error
      for a poll that could not read the list. It is rewritten after every
      poll and attached to Allure at the end, with a screenshot of each poll.

    Flow: login as each user (own browser context) -> every POLL_INTERVAL_S
    until MONITOR_S: reload each session list and count Active rows -> write
    CSV -> close contexts
    """
    contexts, pages, rows, errors = [], {}, [], []
    try:
        for user in USERS:
            context = browser.new_context(**{**browser_context_args, "ignore_https_errors": True})
            contexts.append(context)
            page = context.new_page()
            pages[user] = (page, login_to_posit_workbench(page, user=user))
        _live(capsys, "counting Active sessions of users %s every %.0fs for %.0fs, into %s"
              % (", ".join(str(u) for u in USERS), POLL_INTERVAL_S, MONITOR_S, CSV_PATH))

        started = time.time()
        poll = 0
        while True:
            poll += 1
            poll_started = time.time()
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elapsed = "%.0f" % (poll_started - started)
            total_active = total_listed = failed = 0
            for user, (page, home_url) in pages.items():
                try:
                    goto_session_list(page, home_url)
                    statuses = session_status_counts(page)
                except Exception as exc:
                    failed += 1
                    errors.append("poll %d, user %d: %s" % (poll, user, exc))
                    rows.append([stamp, elapsed, poll, "user %d" % user, "", "", "", "error: %s" % exc])
                    _live(capsys, "poll %d at %s: could not read user %d's session list: %s" % (poll, stamp, user, exc))
                    save_screenshot(page, "active_sessions_failed_U%d_poll%d.png" % (user, poll), always=True)
                    continue
                active, listed = statuses.get("Active", 0), sum(statuses.values())
                total_active += active
                total_listed += listed
                rows.append([stamp, elapsed, poll, "user %d" % user, active, listed,
                             "; ".join("%s=%d" % kv for kv in sorted(statuses.items())), ""])
                save_screenshot(page, "active_sessions_U%d_poll%d.png" % (user, poll))
            if len(pages) > 1:
                rows.append([stamp, elapsed, poll, "total", total_active, total_listed, "",
                             "%d user(s) could not be read" % failed if failed else ""])
            _write_csv(rows)
            _live(capsys, "poll %d at %s (+%ss): %d active session(s) of %d listed (users %s)%s"
                  % (poll, stamp, elapsed, total_active, total_listed, ", ".join(str(u) for u in USERS),
                     " - %d user(s) could not be read" % failed if failed else ""))

            # Polls start POLL_INTERVAL_S apart (a poll that takes longer is
            # followed by the next one straight away).
            next_poll = poll_started + POLL_INTERVAL_S
            if next_poll - started > MONITOR_S:
                break
            time.sleep(max(0.0, next_poll - time.time()))
    finally:
        if rows:
            _write_csv(rows)
            attach_allure_file(CSV_PATH)
            _live(capsys, "%d poll(s) written to %s" % (len({r[2] for r in rows}), CSV_PATH))
        for context in contexts:
            context.close()

    assert not errors, "polls that could not read a session list: %s" % errors
