"""Functional check: a single new RStudio Pro session reaches an active IDE.
Records the launch time and force-quits the session.
"""
import pytest

from common.helper_function import login_and_snapshot
from common.rstudio_session_helper import is_session_alive
from common.session_actions import launch_session
from common.session_cleanup import cleanup_and_verify_sessions

pytestmark = pytest.mark.rstudio_local


def test_new_session_reaches_active_ide(page):
    """A brand-new session opens its IDE; prints the launch time.

    Flow: login -> launch new session -> check IDE is active -> quit session
    """
    home_url, before_ids = login_and_snapshot(page)

    try:
        launch = launch_session(page, home_url)
        assert is_session_alive(page), "session %s is not active after launch" % launch.session_name

        print("\n[rstudio-local] session %s launched in %.2fs" % (launch.session_name, launch.elapsed_s))
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)
