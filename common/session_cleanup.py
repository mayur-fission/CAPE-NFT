"""End-of-test teardown: quit the sessions a test created and confirm the
instance was left as the test found it.
"""
import time

from common.evidence import save_screenshot

from common.rstudio_session_helper import (
    AUTOMATION_NAME_PREFIXES,
    bulk_quit_sessions,
    goto_session_list,
    quit_session,
    row_ids,
    session_row_ids_by_name,
    session_row_ids_with_prefix,
)


def cleanup_multiple_sessions(page, ids, timeout_ms=8000, poll_interval_ms=300, max_attempts=3,
                              bulk_timeout_ms=60000, reload_every_ms=10000):
    """Bulk-quit the session rows `ids`, wait up to bulk_timeout_ms for them
    to disappear (reloading the list every reload_every_ms, in case it does
    not refresh itself), then force-quit any stragglers one at a time, up to
    max_attempts each. Never raises - check row_ids() afterwards to see
    what's left.
    """
    ids = set(ids)
    if not ids:
        return

    checked = set()
    try:
        checked = bulk_quit_sessions(page, ids)
    except Exception as exc:
        print("[rstudio-local] cleanup: bulk quit failed: %s" % exc)

    def _wait_until_gone(wanted, wait_ms, reload_ms=None):
        started = time.time()
        deadline = started + wait_ms / 1000.0
        next_reload = started + reload_ms / 1000.0 if reload_ms else None
        while time.time() < deadline and (wanted & row_ids(page)):
            if next_reload and time.time() >= next_reload:
                try:
                    page.reload()
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
                next_reload = time.time() + reload_ms / 1000.0
            page.wait_for_timeout(poll_interval_ms)
        return wanted & row_ids(page)

    if checked:
        started = time.time()
        left = _wait_until_gone(checked, bulk_timeout_ms, reload_every_ms)
        print("[rstudio-local] cleanup: %d of %d bulk-quit session(s) gone after %.1fs"
              % (len(checked) - len(left), len(checked), time.time() - started))
        if left:
            save_screenshot(page, "bulk_quit_sessions_left", always=True)

    for row_id in _wait_until_gone(ids, timeout_ms):
        print("[rstudio-local] cleanup: %s still listed after the bulk quit, force-quitting it on its own" % row_id)
        for attempt in range(max_attempts):
            try:
                quit_session(page, row_id)
            except Exception:
                pass
            if not _wait_until_gone({row_id}, timeout_ms):
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
