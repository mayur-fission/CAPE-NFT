"""End-of-test cleanup of a multi-browser test from a single browser, the
way test_delete_sessions quits sessions: every browser but the first closes
as soon as it is done; the first waits for them to close, then finds every
session the test created on the session list and quits them
CLEANUP_BATCH_SIZE (25) at a time - tick a batch, click Quit, wait, repeat
until none are left.

Flow: each browser closes its tabs -> SingleBrowserCleanup.run() (no-op but
      in the first browser) -> other browsers close -> first browser: session
      list -> quit in batches of 25 -> verify
"""
import threading

from common.parallel_browsers import run_in_parallel_browsers
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import quit_sessions_in_batches
from common.step_log import log_step
from config.config import env

CLEANUP_BATCH_SIZE = int(env("CLEANUP_BATCH_SIZE", "25"))
# Longest the cleanup browser waits for the other browsers to close
CLEANUP_CLOSE_WAIT_S = int(env("CLEANUP_CLOSE_WAIT_S", "300"))


class SingleBrowserCleanup:
    """Quits every session of a multi-browser run from one browser (the
    first of `prefixes`) once the others have closed. Wire it up with:
      - note_login(prefix, home_url, before_ids) after each browser logs in
      - run(context, prefix) at the end of each browser's worker, after its
        tabs are closed (does nothing except in the first browser)
      - browser_closed as run_in_parallel_browsers(on_closed=...)
      - run_if_missed(...) after run_in_parallel_browsers() returns, in case
        the first browser's thread died before it got to run()
    If the first browser has died meanwhile (crashed, killed as stuck), a
    fresh one is launched and logged in to do the cleanup.
    """

    def __init__(self, prefixes, user, session_names, launch_args, context_args,
                 batch_size=CLEANUP_BATCH_SIZE, close_wait_s=CLEANUP_CLOSE_WAIT_S):
        self.cleaner = prefixes[0]
        self._others = set(prefixes) - {self.cleaner}
        self._user = user
        self._session_names = list(session_names)
        self._launch_args = dict(launch_args or {})
        self._context_args = dict(context_args or {})
        self._batch_size = batch_size
        self._close_wait_s = close_wait_s
        self._cond = threading.Condition()
        self._closed = set()
        self._logged_in = set()
        self._before = []
        self._home_url = None
        self._ran = False
        self.left = []  # sessions still listed after the cleanup

    def note_login(self, prefix, home_url, before_ids):
        """Browser `prefix` logged in; `before_ids` are the rows listed then."""
        with self._cond:
            self._logged_in.add(prefix)
            self._home_url = self._home_url or home_url
            self._before.append(set(before_ids or ()))

    def browser_closed(self, prefix):
        """run_in_parallel_browsers(on_closed=...): browser `prefix` has closed."""
        with self._cond:
            self._closed.add(prefix)
            self._cond.notify_all()

    def run(self, context, prefix):
        """End of browser `prefix`'s worker: in the first browser, wait for
        the others to close, then quit every session of the run. Errors are
        logged, not raised."""
        if prefix != self.cleaner:
            return
        with self._cond:
            self._ran = True
        if self._others:
            log_step("Waiting for the other %d browser(s) to close before the cleanup", len(self._others))
            with self._cond:
                if not self._cond.wait_for(lambda: self._others <= self._closed, self._close_wait_s):
                    log_step("Browser(s) %s still open after %ds - cleaning up anyway",
                             ", ".join(sorted(p.rstrip("_") for p in self._others - self._closed)),
                             self._close_wait_s)
        if not self._logged_in:
            return  # no browser logged in, so none created a session
        try:
            browser = context.browser
            if browser is None or browser.is_connected():
                self._cleanup(context, logged_in=prefix in self._logged_in)
                return
            log_step("Browser %s is no longer running (crashed or closed) - opening a new browser for the cleanup",
                     prefix.rstrip("_"))
            fresh = browser.browser_type.launch(**self._launch_args)
            try:
                self._cleanup(fresh.new_context(**{**self._context_args, "ignore_https_errors": True}),
                              logged_in=False)
            finally:
                fresh.close()
        except Exception as exc:
            self._report_failure(exc)

    def run_if_missed(self, browser_name, launch_args, context_args):
        """After run_in_parallel_browsers() returned: if the first browser's
        thread never got to run() (it died or got stuck), clean up from a new
        browser now."""
        with self._cond:
            if self._ran or not self._logged_in:
                return
            self._ran = True
        log_step("The cleanup browser did not get to the cleanup - opening a new browser for it")

        def _worker(context, _prefix):
            try:
                self._cleanup(context, logged_in=False)
            except Exception as exc:
                self._report_failure(exc)

        run_in_parallel_browsers(browser_name, launch_args, context_args, ["CLEANUP_"], _worker)

    def _cleanup(self, context, logged_in):
        page = context.new_page()
        try:
            home_url = self._home_url
            if not logged_in:
                home_url = login_to_posit_workbench(page, user=self._user)
            # rows listed at every browser's login existed before this run
            before_ids = set.intersection(*self._before) if self._before else set()
            log_step("Cleanup from one browser: quitting the %d session(s) of this test, %d at a time",
                     len(self._session_names), self._batch_size)
            quit_names, self.left = quit_sessions_in_batches(
                page, home_url, self._session_names, before_ids, batch_size=self._batch_size,
            )
            log_step("Cleanup: quit %d RStudio session(s) created by this test%s", len(quit_names),
                     "; still listed (delete them by hand or with test_delete_sessions): %s" % ", ".join(self.left)
                     if self.left else "")
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _report_failure(self, exc):
        log_step("Could not quit the RStudio sessions created by this test (they may need deleting by hand "
                 "or with test_delete_sessions): %s", exc)
