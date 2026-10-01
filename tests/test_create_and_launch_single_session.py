"""Functional check: a single RStudio Pro session can be created and reaches
an active IDE; records the launch time and force-quits the session.
"""
import pytest

from common.session_actions import launch_session, login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions
from common.rstudio_session_helper import is_session_alive, row_ids

pytestmark = pytest.mark.rstudio_local


def test_create_single_session_and_launch(page):
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)

    try:
        launch = launch_session(page, home_url)
        assert is_session_alive(page), "session %s is not active after launch" % launch.session_name

        print("\n[rstudio-local] session %s launched in %.2fs" % (launch.session_name, launch.elapsed_s))
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)
