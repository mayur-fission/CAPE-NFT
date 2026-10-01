"""Functional check: multiple RStudio Pro sessions can be created and each
reaches an active IDE; records per-session launch time and force-quits every
session it created.
"""
import pytest

from common.config import env
from common.helper_function import format_timings
from common.session_actions import launch_session, login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions
from common.rstudio_session_helper import is_session_alive, next_auto_perf_session_names, row_ids

pytestmark = pytest.mark.rstudio_local

SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))


def test_create_multiple_session_and_launch(page):
    home_url = login_to_posit_workbench(page)
    before_ids = row_ids(page)
    session_names = next_auto_perf_session_names(page, SESSION_COUNT, home_url=home_url)

    timings = []
    try:
        for session_name in session_names:
            launch = launch_session(page, home_url, session_name=session_name)
            assert is_session_alive(page), "session %s is not active after launch" % launch.session_name
            timings.append(launch.elapsed_s)

        print(
            "\n[rstudio-local] launched %d sessions; per-session launch time (s): %s"
            % (SESSION_COUNT, format_timings(timings))
        )
    finally:
        cleanup_and_verify_sessions(page, home_url, before_ids)
