"""CONCURRENT_IDLE_SESSIONS sessions (default 5) launched from the UI, named
CONCURRENT_IDLE_SESSION_1 .. _<N>, at most 5 tabs per browser (one browser
per 5 sessions, as in the other concurrent tests), all logged in as one
user. Once every browser has launched its sessions, each tab is just kept
open - nothing is run in it - for CONCURRENT_IDLE_DURATION seconds
(default 600); then every session is force-quit (common/concurrent_idle.py).

Every CONCURRENT_IDLE_CHECK_INTERVAL_S each browser checks its sessions for
drops: the tab no longer shows the IDE, or the session list shows the
session gone or no longer Active/Executing. Any session that drops before
the duration is up fails the test; each drop is logged (SESSION DROP) with
when and how it was seen, screenshotted, and listed in
evidence/session_drops_<TIMINGS_LABEL>.json. Every session list poll goes to
evidence/session_monitor_<TIMINGS_LABEL>_<first session of the browser>.csv.

Per-session idle times go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time to evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json,
with the session time charts (common/session_time_report.py) next to the
CSV, all attached to Allure.
"""
import time

import pytest

from common.concurrent_idle import IDLE_SCRIPT, run_idle_in_concurrent_sessions
from common.parallel_browsers import browsers_for
from common.result_printing import summarize_concurrent_runs
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_naming_logic import CONCURRENT_IDLE_SESSION_PREFIX, concurrent_session_names
from common.session_time_report import describe_run, generate_session_time_report
from config.config import env

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("CONCURRENT_IDLE_SESSIONS", "5"))
# One browser per 5 sessions: never more than 5 tabs in a browser
BROWSER_COUNT = browsers_for(SESSION_COUNT)
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=CONCURRENT_IDLE_SESSION_PREFIX)
# How long the sessions are held idle, and how often each is checked.
DURATION_S = float(env("CONCURRENT_IDLE_DURATION", "600"))
CHECK_INTERVAL_S = float(env("CONCURRENT_IDLE_CHECK_INTERVAL_S", "60"))
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "idle_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, time.strftime("%Y%m%d-%H%M%S"))


def test_concurrent_idle(browser_name, browser_type_launch_args, browser_context_args):
    """No session drops while held idle in its tab.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> console ready -> wait for all browsers ->
    keep the tabs open, checking each session's tab and the session list
    for drops, until CONCURRENT_IDLE_DURATION is up -> wait for all -> close tabs -> quit
    created sessions
    """
    assert SESSION_COUNT >= 1, "CONCURRENT_IDLE_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    records, drops, errors = run_idle_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, DURATION_S, check_interval_s=CHECK_INTERVAL_S, stagger_s=BROWSER_STAGGER_S,
        attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS, retry_wait_s=LAUNCH_RETRY_WAIT_S,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, records)
    summarize_concurrent_runs(records, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Idle time per session (held for %.0fs)" % DURATION_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=IDLE_SCRIPT, group_noun="session",
    )
    if drops:
        print("\n[rstudio-local] %s: %d of %d session(s) dropped before the %.0fs idle hold was up:\n%s" % (
            TIMINGS_LABEL, len(drops), SESSION_COUNT, DURATION_S, "\n".join(
                "  %s - %s, seen in the %s %ss into the hold (%s)"
                % (d["session"], d["status"], d["detected_by"], d["seconds_after_idle_start"], d["time"])
                for d in drops)))
    else:
        print("\n[rstudio-local] %s: no session dropped during the %.0fs idle hold" % (TIMINGS_LABEL, DURATION_S))
    assert not drops, "%d session(s) dropped before the idle hold was up: %s" % (
        len(drops), ", ".join("%s (%s after %ss)" % (d["session"], d["status"], d["seconds_after_idle_start"])
                              for d in drops))
    assert not errors, "\n".join(errors)
