"""End-of-test teardown: quit the sessions a test created and confirm the
instance was left as the test found it.
"""
import time

from common.rstudio_session_helper import bulk_quit_sessions, goto_session_list, quit_session, row_ids


def cleanup_multiple_sessions(page, ids, timeout_ms=8000, poll_interval_ms=300, max_attempts=3):
    """Bulk-quit the session rows `ids`, wait for them to disappear, then
    force-quit any stragglers one at a time, up to max_attempts each. Never
    raises - check row_ids() afterwards to see what's left.
    """
    ids = set(ids)
    if not ids:
        return

    try:
        bulk_quit_sessions(page, ids)
    except Exception:
        pass

    def _wait_until_gone(wanted):
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline and (wanted & row_ids(page)):
            page.wait_for_timeout(poll_interval_ms)
        return wanted & row_ids(page)

    for row_id in _wait_until_gone(ids):
        for attempt in range(max_attempts):
            try:
                quit_session(page, row_id)
            except Exception:
                pass
            if not _wait_until_gone({row_id}):
                break
            if attempt + 1 < max_attempts:
                print(
                    "[rstudio-local] %s still present after quit attempt %d/%d, retrying"
                    % (row_id, attempt + 1, max_attempts)
                )


def cleanup_and_verify_sessions(page, home_url, before_ids):
    """Quit every session created since `before_ids` (from row_ids() at the
    start of the test) and assert none are left. Pre-existing sessions that
    vanished are only reported (someone else's activity on a shared
    instance). Returns the ids that were quit.
    """
    goto_session_list(page, home_url, settle_ms=2000)
    created_ids = row_ids(page) - before_ids

    cleanup_multiple_sessions(page, created_ids)

    page.wait_for_timeout(1000)
    remaining_ids = row_ids(page)
    extra = remaining_ids - before_ids
    missing = before_ids - remaining_ids
    if missing:
        print(
            "\n[rstudio-local] note: %d pre-existing session(s) are gone "
            "that this run did not remove (likely unrelated activity on "
            "this shared instance): %s" % (len(missing), missing)
        )
    assert not extra, "cleanup left session(s) behind: %s" % extra

    return created_ids
