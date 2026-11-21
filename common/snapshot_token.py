"""The snapshot API token, asked for on the console
(tests/test_project_snapshot_time.py).

Start of a run - token_for_run(): the user is asked to paste the token
(Enter keeps the TOKEN in .env). It is checked against the snapshot list API
before any data is changed; a rejected or expired token is asked for again.

Mid-test - ask_new_token(), set as SnapshotApi.on_auth_error: when the API
rejects the token (HTTP 401/403, e.g. it expired during a long snapshot),
the user is asked for a new one and the request is repeated with it instead
of failing the test. The snapshot keeps running on the server meanwhile, and
its time comes from the server's own start/end times, so the wait does not
change it. Enter (no token) fails the test as before.

A new token is used for the rest of the run (later tests too) and, once the
API has accepted it, saved to .env as TOKEN (SNAPSHOT_TOKEN_SAVE=0: not
saved). The token is read hidden and never printed or logged.

Asking needs a console. Without one - Jenkins, the VS Code test explorer,
pytest-xdist workers - or with SNAPSHOT_TOKEN_PROMPT=0, nobody is asked: the
TOKEN in .env is used and a rejected token fails the test.
SNAPSHOT_TOKEN_PROMPT=1 asks even when no console is detected.
"""
import getpass
import os
import sys
from contextlib import nullcontext
from typing import Callable, Optional
from urllib.parse import urlparse

from common.snapshot_api import SnapshotAuthError, clean_token
from common.step_log import log_step
from config.config import REPO_ROOT, env

ENV_FILE = os.path.join(REPO_ROOT, ".env")
# Tokens the user may paste at the start before the run gives up
START_TRIES = 3
_OFF = ("0", "false", "no", "off")
_ON = ("1", "true", "yes", "on")
_pytest_config = None


def use_pytest_config(config):
    """Let the prompts suspend pytest's output capture (needed without -s)."""
    global _pytest_config
    _pytest_config = config


def can_prompt() -> bool:
    """Whether a person can be asked for a token on this run's console."""
    mode = (env("SNAPSHOT_TOKEN_PROMPT") or "auto").strip().lower()
    if mode in _OFF or os.environ.get("PYTEST_XDIST_WORKER"):
        return False
    if mode in _ON:
        return True
    try:
        return bool(sys.__stdin__ and sys.__stdin__.isatty())
    except (ValueError, OSError):
        return False


def _ask(message: str, enter_means: str) -> Optional[str]:
    """Show `message` and read a token, hidden; None when nothing is entered."""
    capman = _pytest_config.pluginmanager.getplugin("capturemanager") if _pytest_config else None
    with capman.global_and_fixture_disabled() if capman else nullcontext():
        print("\n" + "=" * 78 + "\n" + message, flush=True)
        try:
            value = getpass.getpass("Snapshot API token (input hidden; Enter = %s): " % enter_means)
        except EOFError:
            value = ""
        print("=" * 78, flush=True)
    return clean_token(value) or None


def save_token(token: str):
    """Use `token` for the rest of the run and save it to .env as TOKEN
    (unless SNAPSHOT_TOKEN_SAVE=0)."""
    os.environ["TOKEN"] = token
    if (env("SNAPSHOT_TOKEN_SAVE") or "1").strip().lower() in _OFF:
        log_step("New snapshot API token used for this run (not saved: SNAPSHOT_TOKEN_SAVE=0)")
        return
    try:
        from dotenv import set_key

        set_key(ENV_FILE, "TOKEN", token, quote_mode="never")
        log_step("New snapshot API token saved to .env (TOKEN)")
    except Exception as exc:  # noqa: BLE001 - saving is a convenience; the run goes on with the token
        log_step("WARNING: could not save the new token to .env (%s); it is used for this run only",
                 type(exc).__name__)


def token_for_run(check: Callable[[str], Optional[str]]) -> str:
    """The token for this run. Asks the user for it when possible (Enter
    keeps the TOKEN in .env) and checks it with `check(token)`, which returns
    None when the API accepts it, else why not; a rejected token is asked for
    again, up to START_TRIES times. Without a console the TOKEN in .env is
    checked and used. Raises SnapshotAuthError when no usable token is given."""
    current = clean_token(env("TOKEN"))
    if not can_prompt():
        if not current:
            raise SnapshotAuthError("TOKEN is not set: put the snapshot API bearer token in .env as TOKEN=<token> "
                                    "(nobody can be asked for it on this console)")
        problem = check(current)
        if problem:
            raise SnapshotAuthError("the TOKEN in .env was rejected: %s" % problem)
        return current

    host = urlparse(env("API_BASE_URL") or "").netloc or "the snapshot API"
    message = "Snapshot test: paste the bearer token for %s." % host
    for _ in range(START_TRIES):
        entered = clean_token(_ask(message, "use the TOKEN in .env" if current else "stop")) or None
        token = entered or current
        if not token:
            message = "No token given and there is no TOKEN in .env. Paste the bearer token for %s." % host
            continue
        problem = check(token)
        if problem is None:
            if entered and entered != current:
                save_token(entered)
            else:
                os.environ["TOKEN"] = token
            log_step("Snapshot API token accepted (%s)", "the one entered" if entered else "TOKEN from .env")
            return token
        log_step("Snapshot API token rejected: %s", problem)
        message = "That token was rejected: %s\nPaste a valid bearer token for %s." % (problem, host)
    raise SnapshotAuthError("no token the snapshot API accepts after %d tries" % START_TRIES)


def ask_new_token(problem: str) -> Optional[str]:
    """SnapshotApi.on_auth_error: ask the user for a new token after the API
    rejected the current one (`problem`). None when nobody can be asked or
    they press Enter - the test then fails."""
    if not can_prompt():
        return None
    return _ask("The snapshot API rejected the token in the middle of the test:\n  %s\n"
                "Paste a new token to carry on - the snapshot keeps running on the server and its time "
                "comes from the server, so waiting here does not change it." % problem[:400],
                "fail the test")
