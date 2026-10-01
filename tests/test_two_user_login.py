"""Proof of concept: two Workbench users (RSTUDIO_USER1 / RSTUDIO_USER2)
signed in at once, each in its own isolated browser context. Creates no
sessions - it only proves the dual-login wiring other two-user tests use.
"""
import pytest

from common import locators
from common.session_actions import login_to_posit_workbench

pytestmark = pytest.mark.rstudio_local


def test_two_users_login_in_separate_browsers(browser):
    context1 = browser.new_context(ignore_https_errors=True)
    context2 = browser.new_context(ignore_https_errors=True)
    try:
        page1 = context1.new_page()
        page2 = context2.new_page()

        home1 = login_to_posit_workbench(page1, user=1)
        home2 = login_to_posit_workbench(page2, user=2)

        # Both pages - not just one - independently reached the session list.
        assert page1.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.is_visible()
        assert page2.get_by_text(locators.NEW_SESSION_TEXT, exact=True).first.is_visible()

        print("\n[rstudio-local] user 1 signed in at %s; user 2 signed in at %s" % (home1, home2))
    finally:
        context1.close()
        context2.close()
