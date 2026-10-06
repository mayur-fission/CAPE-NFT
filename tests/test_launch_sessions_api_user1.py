"""User 1 (RSTUDIO_USER1) manages RStudio sessions purely through the Workbench
API, with no browser: one session per name in testdata/group_test_sessions.csv.
"""
import pytest

from common.api_helper import (
    check_and_relaunch_inactive_sessions,
    get_testdata_path,
    launch_sessions_from_csv,
)

CSV_PATH = get_testdata_path("group_test_sessions.csv")


def test_user1_launch_sessions_from_csv_via_api():
    """Every name in the CSV gets a new session; the ids are saved to
    testdata/session_ids_U1.csv for later tests to reuse.

    Flow: read session names from CSV -> launch each via API -> save ids
    """
    launched, failed = launch_sessions_from_csv(CSV_PATH, user=1)

    assert not failed, "sessions that failed to launch: %s" % failed
    assert launched, "no session names found in %s" % CSV_PATH


@pytest.mark.skip()
def test_user1_relaunch_inactive_sessions_from_csv_via_api():
    """Every session named in the CSV is running (or starting) afterwards.

    Flow: read session names from CSV -> look up their states via API ->
    resume suspended ones -> relaunch missing or ended ones
    """
    outcome = check_and_relaunch_inactive_sessions(CSV_PATH, user=1)

    assert not outcome["failed"], (
        "sessions that could not be resumed/relaunched: %s" % outcome["failed"]
    )
