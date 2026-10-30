"""Functional check: RSTUDIO_SESSION_COUNT new RStudio Pro sessions, launched
one after another, each reach an active IDE. Records per-session launch time
and force-quits every session created.
"""
import pytest

from config.config import env
from common.session_actions import login_and_plan_session_names
from common.result_printing import format_timings
from common.rstudio_session_helper import is_session_alive
from common.session_actions import launch_session
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_naming_logic import AUTO_PERF_NAME_PREFIX

pytestmark = pytest.mark.rstudio_local

SESSION_COUNT = int(env("RSTUDIO_SESSION_COUNT", "10"))


def test_multiple_new_sessions_reach_active_ide(page):
    """SESSION_COUNT new sessions, launched one after another, each open
    their IDE; prints every launch time.

    Flow: login -> reserve AUTO_PERF_SESSION_<N> names -> launch each session
    in turn -> check its IDE is active -> quit sessions
    """
    home_url, before_ids, session_names = login_and_plan_session_names(
        page, AUTO_PERF_NAME_PREFIX, SESSION_COUNT
    )

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
        cleanup_and_verify_sessions(page, home_url, before_ids, session_names=session_names)
