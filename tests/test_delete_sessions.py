"""Housekeeping: quit every session on the session list named
<DELETE_SESSIONS_PREFIX>* (default AUTO_PERF_SESSION_*), e.g. ones a run
left behind. Uses the same bulk quit as every test's cleanup: tick their
checkboxes and click Quit once, in rounds until none are left. Other
sessions are left alone. Passes when there is nothing to delete.

Careful: it also quits AUTO_PERF_SESSION_* sessions that a test running at
the same time as the same user is still using.
"""
import pytest

from common.config import env
from common.rstudio_session_helper import AUTO_PERF_NAME_PREFIX
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions

pytestmark = pytest.mark.rstudio_local

NAME_PREFIX = env("DELETE_SESSIONS_PREFIX", AUTO_PERF_NAME_PREFIX)
USER = int(env("DELETE_SESSIONS_USER", "1"))


def test_delete_sessions(page):
    """Flow: login -> find every <NAME_PREFIX>* session -> bulk quit them
    -> check none are left
    """
    home_url = login_to_posit_workbench(page, user=USER)
    # No sessions count as "already there", so every matching one is quit
    deleted = cleanup_and_verify_sessions(page, home_url, before_ids=set(), name_prefixes=(NAME_PREFIX,))
    print("\n[rstudio-local] deleted %d %s* session(s)" % (len(deleted), NAME_PREFIX))
