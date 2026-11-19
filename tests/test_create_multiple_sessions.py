"""Create and launch CREATE_MULTIPLE_SESSIONS sessions (default 5), set the
working directory in each, then leave them idle for CREATE_MULTIPLE_IDLE_S
seconds (default 600 = 10 minutes).

Sessions are created the way test_mrgsolve_concurrent creates them: launched
from the UI, spread over CREATE_MULTIPLE_SESSIONS_BROWSERS browsers (blank =
one browser per 5 sessions) started CONCURRENT_SESSIONS_BROWSER_STAGGER_S
apart, all logged in as CONCURRENT_SESSIONS_USER, leftovers with the same
names quit first, each launch retried CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS
times. Names: CREATE_MULTIPLE_[<CREATE_MULTIPLE_SESSION_SUFFIX>_]S1 .. S<N>.

Once launched, each session setwd()s to CREATE_MULTIPLE_WORKING_DIR (blank =
WORKBENCH_HOME_DIR, else RSTUDIO_WORKING_DIR). When every browser is ready,
nothing more is run: the tabs stay open for the idle time, and every
CREATE_MULTIPLE_CHECK_INTERVAL_S (default 60) each browser checks its
sessions for drops (tab no longer shows the IDE, or the session list shows
the session gone / no longer Active) - common/concurrent_idle.py. Then the
sessions are quit.

A session dropped before the idle time is up fails the test. Per-session
idle times go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv, the
session list polls to evidence/session_monitor_<TIMINGS_LABEL>_*.csv, drops
to evidence/session_drops_<TIMINGS_LABEL>.json, all attached to Allure.

    pytest tests/test_create_multiple_sessions.py -m rstudio_local
"""
import time

import pytest

from common.concurrent_idle import IDLE_SCRIPT, run_idle_in_concurrent_sessions
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_naming_logic import create_multiple_session_names
from common.session_time_report import describe_run, generate_session_time_report
from config.config import env

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("CREATE_MULTIPLE_SESSIONS", "5"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("CREATE_MULTIPLE_SESSIONS_BROWSERS") or 0))
SESSION_SUFFIX = env("CREATE_MULTIPLE_SESSION_SUFFIX", "")
# CREATE_MULTIPLE_[<suffix>_]S<N>
SESSION_NAMES = create_multiple_session_names(SESSION_COUNT, SESSION_SUFFIX)
# Checked in the test, not here: a missing setting skips only this test
WORKING_DIR = env("CREATE_MULTIPLE_WORKING_DIR") or env("WORKBENCH_HOME_DIR") or env("RSTUDIO_WORKING_DIR")
# How long the sessions are left idle, and how often they are checked
IDLE_S = float(env("CREATE_MULTIPLE_IDLE_S") or 600)
CHECK_INTERVAL_S = float(env("CREATE_MULTIPLE_CHECK_INTERVAL_S") or 60)
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
TIMINGS_LABEL = "create_multiple_%d_sessions_%d_browsers_U%d%s_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, "_" + SESSION_SUFFIX if SESSION_SUFFIX else "",
    time.strftime("%Y%m%d-%H%M%S"))


def test_create_multiple_sessions(browser_name, browser_type_launch_args, browser_context_args):
    """Every session launches, gets its working directory set, and stays
    alive while idle for CREATE_MULTIPLE_IDLE_S.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> console ready -> setwd -> wait for all
    browsers -> idle, checking for drops every CREATE_MULTIPLE_CHECK_INTERVAL_S
    -> wait for all -> close tabs -> quit created sessions
    """
    if not WORKING_DIR:
        pytest.skip("set CREATE_MULTIPLE_WORKING_DIR (or WORKBENCH_HOME_DIR / RSTUDIO_WORKING_DIR) in .env")
    assert SESSION_COUNT >= 1, "CREATE_MULTIPLE_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    print("\n[rstudio-local] %s: %d session(s) over %d browser(s), working directory %s, idle for %.0fs"
          % (TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT, WORKING_DIR, IDLE_S))
    records, drops, errors = run_idle_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, IDLE_S, check_interval_s=CHECK_INTERVAL_S, stagger_s=BROWSER_STAGGER_S,
        attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
        working_dir=WORKING_DIR,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, records)
    summarize_concurrent_runs(records, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Idle time per session (held for %.0fs)" % IDLE_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=IDLE_SCRIPT, group_noun="session",
    )
    if drops:
        print("\n[rstudio-local] %s: %d of %d session(s) dropped before the %.0fs idle time was up:\n%s" % (
            TIMINGS_LABEL, len(drops), SESSION_COUNT, IDLE_S, "\n".join(
                "  %s - %s, seen in the %s %ss into the idle time (%s)"
                % (d["session"], d["status"], d["detected_by"], d["seconds_after_idle_start"], d["time"])
                for d in drops)))
    else:
        print("\n[rstudio-local] %s: no session dropped during the %.0fs idle time" % (TIMINGS_LABEL, IDLE_S))
    assert not drops, "%d session(s) dropped before the idle time was up: %s" % (
        len(drops), ", ".join("%s (%s after %ss)" % (d["session"], d["status"], d["seconds_after_idle_start"])
                              for d in drops))
    assert not errors, "\n".join(errors)
