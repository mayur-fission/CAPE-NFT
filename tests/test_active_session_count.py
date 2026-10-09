"""Monitor how many Workbench sessions are Active: every
ACTIVE_SESSIONS_POLL_INTERVAL_S (10 s) for ACTIVE_SESSIONS_MONITOR_S, count
the Active rows on each user's session list and record the counts. Run it
alongside the load tests (Jenkins RUN_IN_PARALLEL) to see the session count
over the run. Creates and quits no sessions.

The Workbench home page only lists the signed-in user's own sessions (the
admin dashboard is not enabled), so the total is over the users in
ACTIVE_SESSIONS_USERS (e.g. "1,2"), not the whole server.
"""
import os
import time
from datetime import datetime

import pytest

from common.config import EVIDENCE_DIR, env, env_list
from common.evidence import save_screenshot
from common.rstudio_session_helper import get_active_session_count, goto_session_list, row_ids
from common.session_actions import login_to_posit_workbench
from config.perf_report import write_csv_report

pytestmark = pytest.mark.rstudio_local

USERS = [int(u) for u in env_list("ACTIVE_SESSIONS_USERS", ["1"])]
POLL_INTERVAL_S = float(env("ACTIVE_SESSIONS_POLL_INTERVAL_S", "10"))
MONITOR_S = float(env("ACTIVE_SESSIONS_MONITOR_S", "300"))
CSV_PATH = os.path.join(EVIDENCE_DIR, "active_session_counts.csv")


def test_active_session_count_every_10_seconds(browser, browser_context_args):
    """Every poll reads every user's session list. Counts go to
    evidence/active_session_counts.csv (one row per poll and user, plus a
    total row), attached to Allure with a screenshot of each poll.

    Flow: login as each user (own browser context) -> every POLL_INTERVAL_S
    until MONITOR_S: reload each session list and count Active rows -> write
    CSV -> close contexts
    """
    contexts, pages = [], {}
    rows = [["Time", "User", "Active Sessions", "Listed Sessions"]]
    errors = []
    try:
        for user in USERS:
            context = browser.new_context(**{**browser_context_args, "ignore_https_errors": True})
            contexts.append(context)
            page = context.new_page()
            pages[user] = (page, login_to_posit_workbench(page, user=user))

        started = time.time()
        poll = 0
        while True:
            poll += 1
            poll_started = time.time()
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            total_active = total_listed = 0
            for user, (page, home_url) in pages.items():
                try:
                    goto_session_list(page, home_url)
                    active, listed = get_active_session_count(page), len(row_ids(page))
                except Exception as exc:
                    errors.append("poll %d, user %d: %s" % (poll, user, exc))
                    print("\n[rstudio-local] poll %d: could not read user %d's session list: %s" % (poll, user, exc))
                    save_screenshot(page, "active_sessions_failed_U%d_poll%d.png" % (user, poll), always=True)
                    continue
                total_active += active
                total_listed += listed
                rows.append([stamp, "user %d" % user, active, listed])
                save_screenshot(page, "active_sessions_U%d_poll%d.png" % (user, poll))
            if len(pages) > 1:
                rows.append([stamp, "total", total_active, total_listed])
            print("\n[rstudio-local] poll %d (%s): %d active session(s) of %d listed (users %s)"
                  % (poll, stamp, total_active, total_listed, ", ".join(str(u) for u in USERS)))

            # Polls start POLL_INTERVAL_S apart, however long a poll took.
            next_poll = poll_started + POLL_INTERVAL_S
            if next_poll - started > MONITOR_S:
                break
            time.sleep(max(0.0, next_poll - time.time()))
    finally:
        if len(rows) > 1:
            write_csv_report(CSV_PATH, rows)
            print("[rstudio-local] wrote active session counts to %s" % CSV_PATH)
        for context in contexts:
            context.close()

    assert not errors, "polls that could not read a session list: %s" % errors
