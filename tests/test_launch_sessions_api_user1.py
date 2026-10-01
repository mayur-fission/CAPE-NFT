"""Functional check: user 1 (RSTUDIO_USER1) can launch one RStudio session
per name in testdata/session_names_U1.csv through the Workbench API
(POST /api/launch_session), with no browser involved. The session ids are
written to testdata/session_ids_U1.csv for relaunching later.
"""

import os

from common.api_helper import (
    check_and_relaunch_inactive_sessions,
    launch_sessions_from_csv,
)

CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "testdata",
    "session_names_U1.csv",
)


def test_launch_sessions_from_csv_user1():
    launched, failed = launch_sessions_from_csv(CSV_PATH, user=1)

    assert launched, "no session names found in %s" % CSV_PATH
    assert not failed, "sessions that failed to launch: %s" % failed


def test_check_and_relaunch_inactive_sessions_user1():
    """Every session named in the CSV is running (or on its way) afterwards."""
    outcome = check_and_relaunch_inactive_sessions(CSV_PATH, user=1)

    assert not outcome["failed"], (
        "sessions that could not be resumed/relaunched: %s" % outcome["failed"]
    )
