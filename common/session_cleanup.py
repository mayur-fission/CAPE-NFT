"""End-of-test teardown: quit the sessions a test created and confirm the
instance was left as the test found it.
"""
import time

from common.rstudio_session_helper import (
    AUTOMATION_NAME_PREFIXES,
    bulk_quit_sessions,
    goto_session_list,
    quit_session,
    row_ids,
    session_row_ids_by_name,
    session_row_ids_with_prefix,
)


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


def quit_sessions_named(page, home_url, session_names):
    """Quit every listed session (in any state) whose name is in
    `session_names`, so they can be created afresh. Returns the names that
    were quit; raises RuntimeError if any are still listed afterwards.
    """
    goto_session_list(page, home_url, settle_ms=2000)
    ids_by_name = session_row_ids_by_name(page, session_names)
    if not ids_by_name:
        return []
    print("\n[rstudio-local] quitting existing session(s) with the same name: %s" % ", ".join(sorted(ids_by_name)))

    cleanup_multiple_sessions(page, set().union(*ids_by_name.values()))
    page.wait_for_timeout(1000)
    left = sorted(session_row_ids_by_name(page, ids_by_name))
    if left:
        raise RuntimeError("existing sessions that could not be quit: %s" % left)
    return sorted(ids_by_name)


def cleanup_and_verify_sessions(page, home_url, before_ids, session_names=None,
                                name_prefixes=AUTOMATION_NAME_PREFIXES):
    """Quit the sessions this test created and assert none are left: rows
    not in `before_ids` (from row_ids() at the start of the test) that are
    named one of `session_names`, or, if no names are given, whose name
    starts with one of `name_prefixes`.

    Other new rows (other people's sessions on a shared account, Workbench
    job rows, ...) are left alone and only reported, as are pre-existing
    sessions that vanished. Returns the ids that were quit.
    """
    goto_session_list(page, home_url, settle_ms=2000)
    new_ids = row_ids(page) - before_ids
    if session_names is not None:
        ours = set().union(set(), *session_row_ids_by_name(page, session_names).values())
    else:
        ours = session_row_ids_with_prefix(page, name_prefixes)
    created_ids = new_ids & ours

    cleanup_multiple_sessions(page, created_ids)

    page.wait_for_timeout(1000)
    remaining_ids = row_ids(page)
    left = remaining_ids & created_ids
    others = remaining_ids - before_ids - created_ids
    missing = before_ids - remaining_ids
    if others:
        print(
            "\n[rstudio-local] note: left %d new session(s) alone that this run "
            "did not create: %s" % (len(others), others)
        )
    if missing:
        print(
            "\n[rstudio-local] note: %d pre-existing session(s) are gone "
            "that this run did not remove (likely unrelated activity on "
            "this shared instance): %s" % (len(missing), missing)
        )
    assert not left, "cleanup left session(s) behind: %s" % left

    return created_ids
