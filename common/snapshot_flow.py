"""The project snapshot timing flow shared by the snapshot tests
(tests/test_project_snapshot_time.py, tests/test_initial_50gb.py):

    login -> open the project's session -> change its data -> API 1 create
    snapshot -> API 2 status every SNAPSHOT_POLL_INTERVAL_S until success ->
    snapshot time (finished_at - started_at) -> API 3 snapshot list check ->
    result to the log, Allure, evidence/runs/<run>/snapshot_time_*.json,
    evidence/snapshot_times.csv and the report (HTML, Markdown, PNG)

Settings come from .env (see the test modules' docstrings); the token from
the snapshot_token fixture (common/snapshot_token.py). A test imports the
fixture and calls check_settings() and snapshot_after().
"""
import json
import posixpath
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import pytest
import requests

from common.api_ui_combo import launch_or_reopen_session
from common.dummy_data import create_test_data
from common.perf_report import allure_step
from common.rstudio_console_commands import wait_for_console_ready
from common.rstudio_session_helper import is_session_listed
from common.session_actions import login_to_posit_workbench, open_existing_session
from common.session_naming_logic import SNAPSHOT_SESSION_NAME
from common.snapshot_api import (
    ENDED_FIELDS,
    ERROR_FIELDS,
    FAILED_STATUSES,
    LISTED_SNAPSHOT_ID_FIELDS,
    SNAPSHOT_COMMENT,
    SNAPSHOT_IS_MAJOR,
    SNAPSHOT_NAME,
    STARTED_FIELDS,
    STATUS_FIELDS,
    SUCCESS_STATUSES,
    SnapshotApi,
    SnapshotAuthError,
    SnapshotTimeout,
    created_snapshot_id,
    field,
    listed_snapshot_mismatches,
    project_id_from_path,
    queue_wait_s,
    record_snapshot_result,
    step_durations,
)
from common.snapshot_token import ask_new_token, can_prompt, save_token, token_for_run, use_pytest_config
from common.step_log import log_step
from config.config import RUN_STAMP, env, optional_int

PROJECT_DIR = (env("SNAPSHOT_PROJECT_DIR") or "").rstrip("/")
PROJECT_ID_IN_DIR = project_id_from_path(PROJECT_DIR)
PROJECT_ID = env("PROJECT_ID") or PROJECT_ID_IN_DIR
SESSION_NAME = env("SNAPSHOT_SESSION_NAME") or SNAPSHOT_SESSION_NAME
# The data folder of the add/modify/remove cycle (the initial 50 GB test has its own)
DATA_DIR = env("SNAPSHOT_DATA_DIR") or (posixpath.join(PROJECT_DIR, "snapshot_test_data") if PROJECT_DIR else None)
DATA_SEED = optional_int(env("SNAPSHOT_DATA_SEED"))
DATA_CLEAN = (env("SNAPSHOT_DATA_CLEAN") or "0") in ("1", "true", "True", "yes")
POLL_INTERVAL_S = float(env("SNAPSHOT_POLL_INTERVAL_S") or "10")
SNAPSHOT_TIMEOUT_S = float(env("SNAPSHOT_TIMEOUT_S") or "7200")
DATA_TIMEOUT_S = int(env("SNAPSHOT_DATA_TIMEOUT_S") or "1800")
USER = int(env("SNAPSHOT_USER") or "1")
# Launch SESSION_NAME from the New Session dialog when it is not on the session list
CREATE_SESSION = (env("SNAPSHOT_CREATE_SESSION") or "1") in ("1", "true", "True", "yes")

_REQUIRED = {"SNAPSHOT_PROJECT_DIR (a .../CAPE_<year>_<month>_<id> folder) or PROJECT_ID": PROJECT_ID,
             "SNAPSHOT_PROJECT_DIR or SNAPSHOT_DATA_DIR": DATA_DIR,
             "API_BASE_URL": env("API_BASE_URL")}  # TOKEN: see the snapshot_token fixture


def data_timeout_s(size_gb: float) -> int:
    """Time allowed to change the data: SNAPSHOT_DATA_TIMEOUT_S, at least 120 s per GB."""
    return int(max(DATA_TIMEOUT_S, 120 * size_gb))


def snapshot_timeout_s(size_gb: float) -> float:
    """Time allowed for the snapshot: SNAPSHOT_TIMEOUT_S, at least 240 s per GB."""
    return max(SNAPSHOT_TIMEOUT_S, 240 * size_gb)


def test_timeout_s(size_gb: float) -> int:
    """Whole test for `size_gb`: SNAPSHOT_TEST_TIMEOUT_S, else the data change +
    the snapshot poll + 15 min for login, upload and spare."""
    return int(env("SNAPSHOT_TEST_TIMEOUT_S") or data_timeout_s(size_gb) + snapshot_timeout_s(size_gb) + 900)


test_timeout_s.__test__ = False  # a helper, not a test (pytest collects test_* names it finds)


def check_settings(sizes=()):
    """Fail the test (before login) on a missing or impossible setting; `sizes`
    are the data sizes in GB it will create."""
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        pytest.fail("set %s in .env (see the test module's docstring)" % "; ".join(missing))
    if sizes and min(sizes) < 1:
        pytest.fail("data size %g GB: make_client_shape.py needs at least 1 (GB) per size" % min(sizes))
    if POLL_INTERVAL_S <= 0 or SNAPSHOT_TIMEOUT_S <= 0 or DATA_TIMEOUT_S <= 0:
        pytest.fail("SNAPSHOT_POLL_INTERVAL_S, SNAPSHOT_TIMEOUT_S and SNAPSHOT_DATA_TIMEOUT_S must be above 0")
    if PROJECT_ID_IN_DIR and str(PROJECT_ID) != PROJECT_ID_IN_DIR:
        pytest.fail("PROJECT_ID %s does not match the project folder %s (project %s)"
                    % (PROJECT_ID, PROJECT_DIR, PROJECT_ID_IN_DIR))


# -- token -------------------------------------------------------------------------

def token_problem(token: str):
    """None when the snapshot API accepts `token`, else why not."""
    try:
        SnapshotApi(token=token).check_token(PROJECT_ID)
    except SnapshotAuthError as exc:
        return str(exc).splitlines()[0][:300]
    except requests.RequestException as exc:
        pytest.fail("the snapshot API %s is not reachable to check the token: %s" % (env("API_BASE_URL"), exc))
    return None


@pytest.fixture(scope="session")
def snapshot_token(pytestconfig):
    """The API token for the whole run, asked for once before the first
    snapshot test (common/snapshot_token.py); it is in os.environ["TOKEN"]
    after. Each test module imports this fixture; token_for_run() asks only
    once per run, however many modules use it."""
    use_pytest_config(pytestconfig)
    check_settings()
    try:
        return token_for_run(token_problem)
    except SnapshotAuthError as exc:
        pytest.fail("snapshot API token: %s" % exc)


def api() -> SnapshotApi:
    """A snapshot API client with the run's current token, asking the user
    for a new one when the API rejects it mid-test."""
    client = SnapshotApi()
    client.on_auth_error = ask_new_token
    client.on_new_token = save_token
    return client


# -- session and data --------------------------------------------------------------

def open_session(page):
    """Log in and open SESSION_NAME (launched when not listed), console ready."""
    home_url = login_to_posit_workbench(page, user=USER)
    if is_session_listed(page, home_url, SESSION_NAME, timeout_ms=10000):
        open_existing_session(page, home_url, SESSION_NAME)
        wait_for_console_ready(page)
    elif CREATE_SESSION:
        log_step("Session %s is not on the session list - launching it (SNAPSHOT_CREATE_SESSION)", SESSION_NAME)
        launch_or_reopen_session(page, home_url, SESSION_NAME)
    else:
        pytest.fail("no session %r on RSTUDIO_USER%d's session list: create it, or set "
                    "SNAPSHOT_CREATE_SESSION=1 to let the test launch it" % (SESSION_NAME, USER))


def add_data(page, size_gb: float, data_dir: Optional[str] = None, seed: Optional[int] = None) -> dict:
    """Generate `size_gb` GB in `data_dir` (default DATA_DIR) with `seed`
    (default SNAPSHOT_DATA_SEED)."""
    return create_test_data(page, data_dir or DATA_DIR, size_gb, timeout_ms=data_timeout_s(size_gb) * 1000,
                            seed=DATA_SEED if seed is None else seed, clean=DATA_CLEAN)


def check_data_size(data: dict, size_gb: float):
    """Warn when the data folder held another size than this cycle's (e.g.
    the modify or remove scenario run alone after another size's add)."""
    held = data.get("bytes_total") if data["action"] == "modify" else data.get("total_bytes")
    if held and abs(held / 1e9 - size_gb) > 0.05 * size_gb:
        log_step("WARNING: the data folder held %.2f GB, not this cycle's %g GB - the result is recorded "
                 "under %g GB", held / 1e9, size_gb, size_gb)


# -- the flow ----------------------------------------------------------------------

def snapshot_after(page, scenario: str, size_gb: float, change_data: Callable, what: str, test: str,
                   label: Optional[str] = None, data_dir: Optional[str] = None):
    """Change the project's data with `change_data(page, size_gb)` (in the
    session), then snapshot it, time it, check it in the snapshot list and
    report. `test` names the test file in the results; `label` (default
    `scenario`) ends the snapshot's name, e.g. "NFR 20261121-014119 2GB remove".

    Flow: login -> open session -> change data -> trigger -> poll -> time -> list check -> report
    """
    name = "%s %gGB %s" % (SNAPSHOT_NAME, size_gb, label or scenario)
    wait_s = snapshot_timeout_s(size_gb)
    log_step("%s: scenario %s with %g GB: project %s, data folder %s, API %s", test, scenario, size_gb,
             PROJECT_ID, data_dir or DATA_DIR, env("API_BASE_URL"))
    client = api()

    with allure_step("%s in session %s" % (what, SESSION_NAME)):
        open_session(page)
        data = change_data(page, size_gb)
        if data["action"] != "add":
            check_data_size(data, size_gb)

    with allure_step("Trigger the snapshot of project %s" % PROJECT_ID):
        left = client.token_seconds_left()
        if left is not None and left < wait_s:
            log_step("WARNING: the token expires in %.0f min, before the %.0f min wait is over - %s",
                     left / 60.0, wait_s / 60.0,
                     "you will be asked for a new one if it is rejected" if can_prompt()
                     else "a long snapshot will fail with HTTP 401")
        triggered_at_utc = datetime.now(timezone.utc).isoformat()
        snapshot_id = client.trigger_snapshot(PROJECT_ID, name=name)

    def result(status, timing=None, client_duration_s=None, listed=None, listed_id=None, error=None):
        """The scenario's result for the log, JSON, CSV and report."""
        timing, listed, last = timing or {}, listed or {}, client.last_status or {}
        return {
            "test": test,
            "scenario": scenario,
            "target_gb": size_gb,  # the data size of this test / cycle
            "project_id": PROJECT_ID,
            "snapshot_id": snapshot_id,  # API 1's job_id
            "created_snapshot_id": listed.get("snapshot_id", listed_id),
            "snapshot_version": listed.get("snapshot_version"),
            "snapshot_size": listed.get("snapshot_size"),  # as the list gives it, e.g. "515.73 MB"
            "commit_id": listed.get("commit_id"),
            "snapshot_name": name,
            "status": status,
            "error": error,
            # what changed before the snapshot: data added, removed or modified
            "data_action": data["action"],
            "data_folder": data.get("folder"),
            "data_gb": data["size_gb"],
            "data_bytes": data["total_bytes"],
            "data_files": data["files"],
            "data_seed": data["seed"],
            "data_seconds": data["seconds"],
            "data_percent": data.get("percent"),  # modify: share of the bytes changed
            "snapshot_duration_s": timing.get("duration_s"),
            "snapshot_started_at": timing.get("started_at") or field(last, STARTED_FIELDS),
            "snapshot_ended_at": timing.get("ended_at") or field(last, ENDED_FIELDS),
            "requested_at": last.get("requested_at"),
            "queue_wait_s": queue_wait_s(last),  # started_at - requested_at: time in the queue
            "step_durations_s": step_durations(client.poll_history),
            "client_duration_s": round(client_duration_s, 3) if client_duration_s is not None else None,
            "polls": client.polls,
            "poll_interval_s": POLL_INTERVAL_S,
            "triggered_at_utc": triggered_at_utc,
            "list_check": "passed" if listed else None,
            "poll_history": client.poll_history,
            "trigger_response": client.trigger_response,
            "last_status_response": client.last_status,
            "snapshot_list_entry": listed or None,
        }

    try:
        with allure_step("Wait for snapshot %s (every %ss, at most %ss)" % (snapshot_id, POLL_INTERVAL_S, wait_s)):
            client_duration_s = client.wait_until_complete(snapshot_id, POLL_INTERVAL_S, wait_s)

        with allure_step("Read the snapshot time from the status response"):
            timing = client.snapshot_timing(snapshot_id)

        with allure_step("Check the snapshot in the snapshot list"):
            # the created snapshot's id, from API 2's "snapshot" (API 1's job_id is not it)
            listed_id = (created_snapshot_id(client.last_status or {})
                         or field(client.trigger_response or {}, LISTED_SNAPSHOT_ID_FIELDS))
            listed = client.get_details(snapshot_id, name=name, listed_id=listed_id)
            expected = {
                "name": name,
                "comment": SNAPSHOT_COMMENT,
                "is_major": SNAPSHOT_IS_MAJOR,
                "project_id": PROJECT_ID,
                "snapshot_id": listed_id,
            }
            problems = listed_snapshot_mismatches(listed, expected)
            assert not problems, "snapshot %r in the snapshot list: %s. Entry: %r" % (
                name, "; ".join(problems), listed)
    except Exception as exc:
        # a snapshot that failed, timed out or is wrong in the list is a result too: record it, then fail
        last = client.last_status or {}
        status = str(field(last, STATUS_FIELDS, "") or "").lower() or "unknown"
        server_error = field(last, ERROR_FIELDS)
        error = "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:300] if str(exc) else "")
        if server_error:
            error += " | server error: %s" % (json.dumps(server_error)[:300])
        if status in FAILED_STATUSES:
            outcome = status  # the server failed or cancelled the job
        elif isinstance(exc, SnapshotTimeout):
            outcome = "timeout"
        elif status in SUCCESS_STATUSES:
            outcome = "list_check_failed"  # the snapshot succeeded but the list does not show it right
        else:
            outcome = "error"  # e.g. the status API kept failing (HTTP 5xx)
        with allure_step("Report the failed snapshot"):
            failed = result(outcome, error=error)
            if outcome == "list_check_failed":
                failed["list_check"] = "failed"
            record_snapshot_result(failed)
        raise

    with allure_step("Report the snapshot time"):
        record_snapshot_result(result(str(field(client.last_status or {}, STATUS_FIELDS, "")).lower(), timing,
                                      client_duration_s, listed, listed_id))


def run_seed() -> int:
    """A seed that differs per run (from the run's start time), so data made
    again in an emptied folder is new to the snapshot, not the same bytes."""
    return int(RUN_STAMP.replace("-", "")) % 2_000_000_000 if RUN_STAMP else int(time.time())
