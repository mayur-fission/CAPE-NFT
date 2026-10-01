"""End-of-run teardown: quit sessions and confirm the instance was left as
the run found it.
"""
import time

from common.rstudio_session_helper import quit_session as _quit_session
from common.rstudio_session_helper import bulk_quit_sessions as _bulk_quit_sessions
from common.rstudio_session_helper import row_ids as _row_ids


def cleanup_session(page, row_id):
    """Force-quit one session (never "Quit All")."""
    _quit_session(page, row_id)


def cleanup_multiple_sessions(page, row_ids, timeout_ms=8000, poll_interval_ms=300, max_attempts=3):
    """Bulk-quit `row_ids`, wait for them to disappear, then retry any
    stragglers one at a time up to max_attempts. Never raises - check
    row_ids() afterwards to see what's left.
    """
    row_ids = set(row_ids)
    if not row_ids:
        return

    try:
        _bulk_quit_sessions(page, row_ids)
    except Exception:
        pass

    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline and (row_ids & _row_ids(page)):
        page.wait_for_timeout(poll_interval_ms)

    remaining = row_ids & _row_ids(page)
    for row_id in remaining:
        for attempt in range(max_attempts):
            try:
                cleanup_session(page, row_id)
            except Exception:
                pass
            row_deadline = time.time() + (timeout_ms / 1000.0)
            while time.time() < row_deadline and row_id in _row_ids(page):
                page.wait_for_timeout(poll_interval_ms)
            if row_id not in _row_ids(page):
                break
            if attempt + 1 < max_attempts:
                print(
                    "[rstudio-local] %s still present after quit attempt %d/%d, retrying"
                    % (row_id, attempt + 1, max_attempts)
                )


def cleanup_and_verify_sessions(page, home_url, before_ids):
    """Quit every session created since before_ids and assert none are left.

    Navigates to home_url first. Pre-existing sessions that vanished are
    only reported (someone else's activity). Returns the ids that were quit.
    """
    page.goto(home_url)
    page.get_by_text("New Session", exact=True).first.wait_for(state="visible", timeout=30000)
    page.wait_for_timeout(2000)
    created_ids = _row_ids(page) - before_ids

    cleanup_multiple_sessions(page, created_ids)

    page.wait_for_timeout(1000)
    remaining_ids = _row_ids(page)
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
