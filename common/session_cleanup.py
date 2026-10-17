"""End-of-test teardown: quit the sessions a test created (bulk Quit,
in rounds, whole list loaded) and check none are left.

Flow: session list -> load all rows -> tick this test's rows -> Quit once
      -> wait -> repeat -> force-quit stragglers -> verify
"""
import time

from common.evidence import save_screenshot

from common.rstudio_session_helper import (
    bulk_quit_sessions,
    goto_session_list,
    load_all_session_rows,
    quit_session,
    row_ids,
    session_row_ids_by_name,
    session_row_ids_with_prefix,
)
from common.session_naming_logic import AUTOMATION_NAME_PREFIXES


def cleanup_multiple_sessions(page, ids, timeout_ms=8000, poll_interval_ms=300, max_attempts=3,
                              bulk_timeout_ms=60000, reload_every_ms=10000, fallback=True):
    """Bulk-quit the session rows `ids`, wait for them to disappear (at least
    bulk_timeout_ms, 0.5 s more per session for big batches, reloading the
    list every reload_every_ms in case it does not refresh itself), then,
    with `fallback`, force-quit any stragglers one at a time, up to
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
        left = _wait_until_gone(checked, max(bulk_timeout_ms, 500 * len(checked)), reload_every_ms)
        print("[rstudio-local] cleanup: %d of %d bulk-quit session(s) gone after %.1fs"
              % (len(checked) - len(left), len(checked), time.time() - started))
        if left:
            save_screenshot(page, "bulk_quit_sessions_left", always=True)

    if not fallback:
        return
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
    load_all_session_rows(page)
    ids_by_name = session_row_ids_by_name(page, session_names)
    if not ids_by_name:
        return []
    print("\n[rstudio-local] quitting existing session(s) with the same name: %s" % ", ".join(sorted(ids_by_name)))

    cleanup_multiple_sessions(page, set().union(*ids_by_name.values()))
    goto_session_list(page, home_url, settle_ms=2000)
    load_all_session_rows(page)
    left = sorted(session_row_ids_by_name(page, ids_by_name))
    if left:
        raise RuntimeError("existing sessions that could not be quit: %s" % left)
    return sorted(ids_by_name)


def cleanup_and_verify_sessions(page, home_url, before_ids, session_names=None,
                                name_prefixes=AUTOMATION_NAME_PREFIXES, max_rounds=4):
    """Quit the sessions this test created - new rows (not in `before_ids`)
    named one of `session_names`, else starting with one of `name_prefixes`
    - and assert none are left. Other new rows are only reported.

    Flow: load all rows -> bulk Quit -> reload -> repeat (max_rounds) ->
          force-quit leftovers one by one in the last round -> verify

    Returns the ids quit.
    """
    def _ours():
        goto_session_list(page, home_url, settle_ms=2000)
        load_all_session_rows(page)
        new_ids = row_ids(page) - before_ids
        if session_names is not None:
            ours = set().union(set(), *session_row_ids_by_name(page, session_names).values())
        else:
            ours = session_row_ids_with_prefix(page, name_prefixes)
        return new_ids & ours

    created_ids, previous = set(), None
    for round_no in range(1, max_rounds + 1):
        targets = _ours()
        if not targets:
            break
        created_ids |= targets
        last_round = round_no == max_rounds or targets == previous
        print("[rstudio-local] cleanup round %d: %d session(s) of this run listed%s"
              % (round_no, len(targets), ", force-quitting leftovers one at a time" if last_round else ""))
        cleanup_multiple_sessions(page, targets, fallback=last_round)
        previous = targets

    left = _ours()
    remaining_ids = row_ids(page)
    others = remaining_ids - before_ids - created_ids - left
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
