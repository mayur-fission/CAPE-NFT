"""Housekeeping: quit every session whose name starts with one of the
prefixes (default: every prefix the UI tests use - session_naming_logic
ALL_SESSION_NAME_PREFIXES), e.g. sessions a run left behind. Other sessions
are left alone. Passes when there is nothing to delete.

Choose the prefixes in .env: DELETE_SESSIONS_PREFIXES=CONCURRENT_BATCH_,RUN_R_CLOSE_SESSION_
(or the older single DELETE_SESSIONS_PREFIX).

Careful: it also quits matching sessions that a test running at the same
time as the same user is still using.
"""
import pytest

from common.evidence import save_screenshot
from common.rstudio_session_helper import (
    goto_session_list,
    listed_session_names,
    load_all_session_rows,
    row_ids,
    session_names_with_prefix,
)
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import cleanup_and_verify_sessions
from common.session_naming_logic import ALL_SESSION_NAME_PREFIXES
from config.config import env, env_list

pytestmark = pytest.mark.rstudio_local

NAME_PREFIXES = tuple(env_list("DELETE_SESSIONS_PREFIXES")
                      or ([env("DELETE_SESSIONS_PREFIX")] if env("DELETE_SESSIONS_PREFIX") else [])
                      or ALL_SESSION_NAME_PREFIXES)
USER = int(env("DELETE_SESSIONS_USER", "1"))


def test_delete_sessions(page):
    """Flow: login -> session list (all rows loaded) -> find every session
    named <prefix>* -> bulk quit them -> check none are left
    """
    home_url = login_to_posit_workbench(page, user=USER)
    goto_session_list(page, home_url, settle_ms=2000)
    listed = load_all_session_rows(page)
    matching = session_names_with_prefix(page, NAME_PREFIXES)

    print("\n[rstudio-local] %d session(s) listed, %d matching %s"
          % (listed, len(matching), ", ".join(p + "*" for p in NAME_PREFIXES)))
    for prefix in NAME_PREFIXES:
        names = sorted(n for n in matching.values() if n.startswith(prefix))
        if names:
            print("[rstudio-local]   %s*: %d - %s" % (prefix, len(names), ", ".join(names)))
    if listed and not matching:
        print("[rstudio-local] no session matched; session names on the list include: %s"
              % (", ".join(listed_session_names(page)) or "(none recognised)"))
        save_screenshot(page, "delete_sessions_none_matched.png", always=True)

    # Every other session counts as "already there", so only the matching ones are quit
    others = row_ids(page) - set(matching)
    deleted = cleanup_and_verify_sessions(page, home_url, before_ids=others, name_prefixes=NAME_PREFIXES)
    print("\n[rstudio-local] deleted %d session(s)" % len(deleted))
