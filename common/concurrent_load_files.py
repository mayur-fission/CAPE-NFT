"""A console command (default list.files(<working dir>)) repeated in many
sessions at once: sessions spread over browsers, all logged in as one user
(common/concurrent_file_crud.py's flow). Each session sends the command,
waits for its answer, waits `wait_s`, and sends it again until the
duration is up; every call is timed.

The command is typed into each tab's console without waiting for the
answer (api_ui_combo.ListFilesLoop), so the tabs of one browser call it at
the same time instead of taking turns.
"""
import time

from common.api_ui_combo import ListFilesLoop
from common.concurrent_file_crud import run_loops_in_concurrent_sessions
from common.rstudio_console_commands import wait_for_console_ready
from common.session_actions import set_session_working_directory
from common.step_log import log_step
from config.config import env

# Time allowed for a call still in flight when the duration is up.
CALL_GRACE_S = float(env("LOAD_FILES_GRACE_S", "60"))


def run_load_files_in_concurrent_sessions(browser_name, launch_args, context_args, user, session_names,
                                          browser_count, label, command, script, duration_s, wait_s,
                                          stagger_s=0, attempts=3, launch_timeout_ms=180000, retry_wait_s=15):
    """Repeat `command` in every one of `session_names`, spread over
    `browser_count` browsers (started `stagger_s` apart), from the moment
    all are ready until `duration_s` later, waiting `wait_s` after each
    answer. Every call's record has "script" = `script` and "loop" = its
    number in the session.

    Returns (records, errors): a run record per call and "browser <B>:
    <error>" per browser with a failed call or launch.
    """

    def _prepare(session):
        wait_for_console_ready(session.page)
        set_session_working_directory(session.page)
        log_step("Session %s ready for %s", session.session_name, command)

    def _run_loops(launched, records):
        calls = ListFilesLoop(launched, None, time.time() + duration_s, wait_s, command=command, script=script)
        records += calls.run_until_deadline(grace_s=CALL_GRACE_S)
        return ["%s call %d: %s" % (r["session_name"], r["loop"], r["status"])
                for r in calls.records if r["status"] != "ok"]

    return run_loops_in_concurrent_sessions(
        browser_name, launch_args, context_args, user, session_names, browser_count, label,
        "%s loops (every %.0fs after the answer, for %.0fs)" % (command, wait_s, duration_s),
        _prepare, _run_loops, duration_s + CALL_GRACE_S, stagger_s=stagger_s, attempts=attempts,
        launch_timeout_ms=launch_timeout_ms, retry_wait_s=retry_wait_s,
    )


def call_summary(records, script):
    """'<script> min 0.10 / avg 0.14 / max 0.17 s over 42 ok call(s)'."""
    times = [r["ended"] - r["started"] for r in records if r.get("script") == script and r["status"] == "ok"]
    if not times:
        return "%s: no ok calls" % script
    return "%s min %.2f / avg %.2f / max %.2f s over %d ok call(s)" % (
        script, min(times), sum(times) / len(times), max(times), len(times))
