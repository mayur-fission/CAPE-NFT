"""Project snapshot time: how long a snapshot of an existing project takes
after its data changes - end time - start time from the snapshot status
API's response on success (the client-side time from the trigger call to
that poll is kept too). One test per change (scenario):

  add     test_snapshot_after_adding_data: generate SNAPSHOT_DATA_GB GB in
          SNAPSHOT_DATA_DIR (python3 make_client_shape.py <N>GB <folder>
          from the R console, see common/dummy_data.py)
  modify  test_snapshot_after_modifying_data: overwrite SNAPSHOT_MODIFY_PERCENT
          (10) % of the bytes in SNAPSHOT_DATA_DIR with new random bytes, in
          place (python3 modify_test_data.py, see common/modify_test_data.py);
          needs the add scenario's data
  remove  test_snapshot_after_removing_data: delete SNAPSHOT_DATA_DIR, the
          data the add scenario made (only a folder holding the generator's
          marker file is deleted)

The tests run in that order (add -> modify -> remove), each on the data the
one before left, so running them all is one full cycle.

Sizes: SNAPSHOT_DATA_GB takes one size or a comma-separated sequence, e.g.
"1, 2, 5, 10, 25, 50": one full cycle per size, in the order given - add,
modify, remove with 1 GB, then add, modify, remove with 2 GB, and so on.
Test ids end in the size (test_snapshot_after_adding_data[2GB-chromium]),
so -k "2GB" runs one size. The data and snapshot timeouts grow with the size
(see _data_timeout_s / _snapshot_timeout_s).

The project is created by hand beforehand; its RStudio session is opened,
or launched when it is not listed yet. Nothing outside SNAPSHOT_DATA_DIR is
deleted.

Flow: login -> open session SNAPSHOT_SESSION_NAME -> change the data
      (scenario) -> API 1 create snapshot of PROJECT_ID (name,
      comment, is_major; client timer starts) -> API 2 snapshot status
      (snapshot-jobs/<id from API 1>) every SNAPSHOT_POLL_INTERVAL_S until
      success (fails at once on failed/error/cancelled, after
      SNAPSHOT_TIMEOUT_S otherwise) -> snapshot time = end - start from that
      response -> API 3 snapshot list: our snapshot (by name) shows the name,
      comment and is_major sent (and snapshot id / project / status when
      listed) -> result to the log, Allure,
      evidence/runs/<run>/snapshot_time.json and evidence/snapshot_times.csv
      (all runs)

Token: before the first test the run asks for the API's bearer token on the
console (Enter keeps the TOKEN in .env) and checks it with the snapshot list
API; if the API rejects the token mid-test, it asks for a new one and carries
on with it instead of failing. A new token is saved to .env as TOKEN. No
console (Jenkins, VS Code test explorer): the TOKEN in .env is used. See
common/snapshot_token.py.

.env / environment:
  required  SNAPSHOT_PROJECT_DIR (the project folder on the server, e.g.
            /mnt/fsx/CAPE_2026_11_0223: project id 223 comes from its name),
            API_BASE_URL (the snapshot API, e.g. https://<host>),
            TOKEN (the API's bearer token, sent as "Bearer <TOKEN>"; asked
            for at the start when the run has a console)
  token     SNAPSHOT_TOKEN_PROMPT (auto: ask when there is a console; 0:
            never; 1: always), SNAPSHOT_TOKEN_SAVE (1: save a new token to
            .env; 0: this run only)
  optional  PROJECT_ID (instead of the one in SNAPSHOT_PROJECT_DIR; must
            match it when both are set), SNAPSHOT_DATA_DIR (the empty data
            folder; <project dir>/snapshot_test_data),
            SNAPSHOT_SESSION_NAME (TEST_SNAPSHOT_NFR, from
            common/session_naming_logic.py), SNAPSHOT_CREATE_SESSION (1:
            launch that session when it is not listed; 0: fail instead),
            SNAPSHOT_DATA_GB (1; decimal GB, at least 1; or a sequence such
            as 1,2,5 - one cycle per size), SNAPSHOT_DATA_SEED
            (the script's default, 1), SNAPSHOT_DATA_CLEAN (0; 1 empties
            SNAPSHOT_DATA_DIR first), SNAPSHOT_POLL_INTERVAL_S (10),
            SNAPSHOT_TIMEOUT_S (7200; at least 240 s per GB),
            SNAPSHOT_DATA_TIMEOUT_S (1800; at least 120 s per GB),
            SNAPSHOT_TEST_TIMEOUT_S (both timeouts of the largest size + 900),
            SNAPSHOT_USER (1),
            SNAPSHOT_DATA_SCRIPT / SNAPSHOT_DATA_SERVER_SCRIPT /
            SNAPSHOT_SCRIPT_DIR (which generator, where it is uploaded),
            SNAPSHOT_NAME (NFR <run start>) / SNAPSHOT_COMMENT (TEST) /
            SNAPSHOT_IS_MAJOR (false) - API 1's body,
            SNAPSHOT_ID_FIELD (the field of API 1's response holding the
            snapshot id), SNAPSHOT_START_FIELD / SNAPSHOT_END_FIELD (API 2's
            start/end time fields), SNAPSHOT_TRIGGER_PATH / _STATUS_PATH /
            _LIST_PATH, SNAPSHOT_LIST_PAGE_SIZE (20) / _LIST_MAX_PAGES (5),
            SNAPSHOT_MAX_POLL_ERRORS (30: failed status requests in a row),
            SNAPSHOT_LIST_ATTEMPTS (6) / _LIST_RETRY_S (10) - how long to wait
            for the snapshot to show in the list after it succeeds

Not in the normal suite (pytest.ini deselects the snapshot marker); one
all three in order (add, modify, remove), or one scenario with -k (adding,
modifying, removing):
    pytest tests/test_project_snapshot_time.py -m snapshot
    pytest tests/test_project_snapshot_time.py -m snapshot -k modifying
    pytest tests/test_project_snapshot_time.py -m snapshot -k "2GB"
"""
import json
import posixpath
from datetime import datetime, timezone

import pytest
import requests

from common.dummy_data import create_test_data, modify_test_data, remove_test_data
from common.api_ui_combo import launch_or_reopen_session
from common.perf_report import allure_step
from common.rstudio_session_helper import is_session_listed
from common.rstudio_console_commands import wait_for_console_ready
from common.session_actions import login_to_posit_workbench, open_existing_session
from common.session_naming_logic import SNAPSHOT_SESSION_NAME
from common.step_log import log_step
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
from config.config import env, optional_int

PROJECT_DIR = (env("SNAPSHOT_PROJECT_DIR") or "").rstrip("/")
PROJECT_ID_IN_DIR = project_id_from_path(PROJECT_DIR)
PROJECT_ID = env("PROJECT_ID") or PROJECT_ID_IN_DIR
SESSION_NAME = env("SNAPSHOT_SESSION_NAME") or SNAPSHOT_SESSION_NAME
DATA_DIR = env("SNAPSHOT_DATA_DIR") or (posixpath.join(PROJECT_DIR, "snapshot_test_data") if PROJECT_DIR else None)


def _parse_sizes(text: str):
    """(sizes in GB, problem): "1, 2, 5" -> ([1.0, 2.0, 5.0], None); a "GB"
    suffix is allowed; repeats are dropped, the order is kept."""
    sizes = []
    for part in text.split(","):
        part = part.strip().upper()
        part = (part[:-2] if part.endswith("GB") else part).strip()
        if not part:
            continue
        try:
            size = float(part)
        except ValueError:
            return [1.0], "SNAPSHOT_DATA_GB %r: %r is not a number (use e.g. 1 or 1,2,5)" % (text, part)
        if size not in sizes:
            sizes.append(size)
    return sizes or [1.0], None


# One full add/modify/remove cycle per size, in this order
DATA_SIZES, DATA_SIZES_PROBLEM = _parse_sizes(env("SNAPSHOT_DATA_GB") or "1")
DATA_SEED = optional_int(env("SNAPSHOT_DATA_SEED"))
DATA_CLEAN = (env("SNAPSHOT_DATA_CLEAN") or "0") in ("1", "true", "True", "yes")
POLL_INTERVAL_S = float(env("SNAPSHOT_POLL_INTERVAL_S") or "10")
SNAPSHOT_TIMEOUT_S = float(env("SNAPSHOT_TIMEOUT_S") or "7200")
DATA_TIMEOUT_S = int(env("SNAPSHOT_DATA_TIMEOUT_S") or "1800")


def _data_timeout_s(size_gb: float) -> int:
    """Time allowed to change the data: SNAPSHOT_DATA_TIMEOUT_S, at least 120 s per GB."""
    return int(max(DATA_TIMEOUT_S, 120 * size_gb))


def _snapshot_timeout_s(size_gb: float) -> float:
    """Time allowed for the snapshot: SNAPSHOT_TIMEOUT_S, at least 240 s per GB."""
    return max(SNAPSHOT_TIMEOUT_S, 240 * size_gb)


# Whole test, for the largest size: data change + the snapshot poll + 15 min for login, upload and spare
TEST_TIMEOUT_S = int(env("SNAPSHOT_TEST_TIMEOUT_S")
                     or _data_timeout_s(max(DATA_SIZES)) + _snapshot_timeout_s(max(DATA_SIZES)) + 900)
USER = int(env("SNAPSHOT_USER") or "1")
# Launch SESSION_NAME from the New Session dialog when it is not on the session list
CREATE_SESSION = (env("SNAPSHOT_CREATE_SESSION") or "1") in ("1", "true", "True", "yes")
# Modify scenario: share of the bytes changed, and the seed picking the files (blank: random, recorded)
MODIFY_PERCENT = float(env("SNAPSHOT_MODIFY_PERCENT") or "10")
MODIFY_SEED = optional_int(env("SNAPSHOT_MODIFY_SEED"))

pytestmark = [pytest.mark.rstudio_local, pytest.mark.snapshot]

_REQUIRED = {"SNAPSHOT_PROJECT_DIR (a .../CAPE_<year>_<month>_<id> folder) or PROJECT_ID": PROJECT_ID,
             "SNAPSHOT_PROJECT_DIR or SNAPSHOT_DATA_DIR": DATA_DIR,
             "API_BASE_URL": env("API_BASE_URL")}  # TOKEN: see the snapshot_token fixture


def _check_settings():
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        pytest.fail("set %s in .env (see the module docstring)" % "; ".join(missing))
    if DATA_SIZES_PROBLEM:
        pytest.fail(DATA_SIZES_PROBLEM)
    if min(DATA_SIZES) < 1:
        pytest.fail("SNAPSHOT_DATA_GB has %g: make_client_shape.py needs at least 1 (GB) per size" % min(DATA_SIZES))
    if not 0 < MODIFY_PERCENT <= 100:
        pytest.fail("SNAPSHOT_MODIFY_PERCENT is %s: use a share of the data above 0 and up to 100" % MODIFY_PERCENT)
    if POLL_INTERVAL_S <= 0 or SNAPSHOT_TIMEOUT_S <= 0 or DATA_TIMEOUT_S <= 0:
        pytest.fail("SNAPSHOT_POLL_INTERVAL_S, SNAPSHOT_TIMEOUT_S and SNAPSHOT_DATA_TIMEOUT_S must be above 0")
    if PROJECT_ID_IN_DIR and str(PROJECT_ID) != PROJECT_ID_IN_DIR:
        pytest.fail("PROJECT_ID %s does not match the project folder %s (project %s)"
                    % (PROJECT_ID, PROJECT_DIR, PROJECT_ID_IN_DIR))


def _token_problem(token: str):
    """None when the snapshot API accepts `token`, else why not."""
    try:
        SnapshotApi(token=token).check_token(PROJECT_ID)
    except SnapshotAuthError as exc:
        return str(exc).splitlines()[0][:300]
    except requests.RequestException as exc:
        pytest.fail("the snapshot API %s is not reachable to check the token: %s" % (env("API_BASE_URL"), exc))
    return None


@pytest.fixture(scope="module", params=DATA_SIZES, ids=lambda gb: "%gGB" % gb)
def data_gb(request) -> float:
    """The data size of this cycle. Module scope makes pytest run every test
    with the first size (add, modify, remove), then every test with the next."""
    log_step("Data size %g GB (cycle %d of %d: %s GB)", request.param, DATA_SIZES.index(request.param) + 1,
             len(DATA_SIZES), ", ".join("%g" % s for s in DATA_SIZES))
    return request.param


@pytest.fixture(scope="session")
def snapshot_token(pytestconfig):
    """The API token for the whole run, asked for once before the first
    test (common/snapshot_token.py); it is in os.environ["TOKEN"] after."""
    use_pytest_config(pytestconfig)
    _check_settings()
    try:
        return token_for_run(_token_problem)
    except SnapshotAuthError as exc:
        pytest.fail("snapshot API token: %s" % exc)


def _api() -> SnapshotApi:
    """A snapshot API client with the run's current token, asking the user
    for a new one when the API rejects it mid-test."""
    api = SnapshotApi()
    api.on_auth_error = ask_new_token
    api.on_new_token = save_token
    return api


def _open_session(page):
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


def _add_data(page, size_gb: float) -> dict:
    return create_test_data(page, DATA_DIR, size_gb, timeout_ms=_data_timeout_s(size_gb) * 1000,
                            seed=DATA_SEED, clean=DATA_CLEAN)


def _remove_data(page, size_gb: float) -> dict:
    return remove_test_data(page, DATA_DIR, timeout_ms=_data_timeout_s(size_gb) * 1000)


def _modify_data(page, size_gb: float) -> dict:
    return modify_test_data(page, DATA_DIR, MODIFY_PERCENT, seed=MODIFY_SEED,
                            timeout_ms=_data_timeout_s(size_gb) * 1000)


def _check_data_size(data: dict, size_gb: float):
    """Warn when the data folder held another size than this cycle's (e.g.
    the modify or remove scenario run alone after another size's add)."""
    held = data.get("bytes_total") if data["action"] == "modify" else data.get("total_bytes")
    if held and abs(held / 1e9 - size_gb) > 0.05 * size_gb:
        log_step("WARNING: the data folder held %.2f GB, not this cycle's %g GB - the result is recorded "
                 "under %g GB", held / 1e9, size_gb, size_gb)


def _snapshot_after(page, scenario: str, size_gb: float, change_data, what: str):
    """Change the project's data with `change_data(page, size_gb)` (in the
    session), then snapshot it, time it, check it in the snapshot list and report.

    Flow: login -> open session -> change data -> trigger -> poll -> time -> list check -> report
    """
    _check_settings()
    name = "%s %gGB %s" % (SNAPSHOT_NAME, size_gb, scenario)  # e.g. "NFR 20261121-014119 2GB remove"
    snapshot_timeout_s = _snapshot_timeout_s(size_gb)
    log_step("Scenario %s with %g GB: project %s, data folder %s, API %s", scenario, size_gb, PROJECT_ID,
             DATA_DIR, env("API_BASE_URL"))
    api = _api()

    with allure_step("%s in session %s" % (what, SESSION_NAME)):
        _open_session(page)
        data = change_data(page, size_gb)
        if data["action"] != "add":
            _check_data_size(data, size_gb)

    with allure_step("Trigger the snapshot of project %s" % PROJECT_ID):
        left = api.token_seconds_left()
        if left is not None and left < snapshot_timeout_s:
            log_step("WARNING: the token expires in %.0f min, before the %.0f min wait is over - %s",
                     left / 60.0, snapshot_timeout_s / 60.0,
                     "you will be asked for a new one if it is rejected" if can_prompt()
                     else "a long snapshot will fail with HTTP 401")
        triggered_at_utc = datetime.now(timezone.utc).isoformat()
        snapshot_id = api.trigger_snapshot(PROJECT_ID, name=name)

    def result(status, timing=None, client_duration_s=None, listed=None, listed_id=None, error=None):
        """The scenario's result for the log, JSON, CSV and report."""
        timing, listed, last = timing or {}, listed or {}, api.last_status or {}
        return {
            "scenario": scenario,
            "target_gb": size_gb,  # this cycle's data size (SNAPSHOT_DATA_GB)
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
            "step_durations_s": step_durations(api.poll_history),
            "client_duration_s": round(client_duration_s, 3) if client_duration_s is not None else None,
            "polls": api.polls,
            "poll_interval_s": POLL_INTERVAL_S,
            "triggered_at_utc": triggered_at_utc,
            "list_check": "passed" if listed else None,
            "poll_history": api.poll_history,
            "trigger_response": api.trigger_response,
            "last_status_response": api.last_status,
            "snapshot_list_entry": listed or None,
        }

    try:
        with allure_step("Wait for snapshot %s (every %ss, at most %ss)" % (snapshot_id, POLL_INTERVAL_S,
                                                                          snapshot_timeout_s)):
            client_duration_s = api.wait_until_complete(snapshot_id, POLL_INTERVAL_S, snapshot_timeout_s)

        with allure_step("Read the snapshot time from the status response"):
            timing = api.snapshot_timing(snapshot_id)

        with allure_step("Check the snapshot in the snapshot list"):
            # the created snapshot's id, from API 2's "snapshot" (API 1's job_id is not it)
            listed_id = (created_snapshot_id(api.last_status or {})
                         or field(api.trigger_response or {}, LISTED_SNAPSHOT_ID_FIELDS))
            listed = api.get_details(snapshot_id, name=name, listed_id=listed_id)
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
        last = api.last_status or {}
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
        record_snapshot_result(result(str(field(api.last_status or {}, STATUS_FIELDS, "")).lower(), timing,
                                      client_duration_s, listed, listed_id))


@pytest.mark.timeout(TEST_TIMEOUT_S)
def test_snapshot_after_adding_data(snapshot_token, data_gb, page):
    """Scenario 1: add data_gb GB of test data, then snapshot."""
    _snapshot_after(page, "add", data_gb, _add_data, "Create %g GB of test data" % data_gb)


@pytest.mark.timeout(TEST_TIMEOUT_S)
def test_snapshot_after_modifying_data(snapshot_token, data_gb, page):
    """Scenario 2: change SNAPSHOT_MODIFY_PERCENT % of the test data (data_gb GB) in place, then snapshot."""
    _snapshot_after(page, "modify", data_gb, _modify_data,
                    "Change %g%% of the %g GB of test data in %s" % (MODIFY_PERCENT, data_gb, DATA_DIR))


@pytest.mark.timeout(TEST_TIMEOUT_S)
def test_snapshot_after_removing_data(snapshot_token, data_gb, page):
    """Scenario 3: delete the test data (data_gb GB, as added or added and modified), then snapshot."""
    _snapshot_after(page, "remove", data_gb, _remove_data,
                    "Remove the %g GB of test data in %s" % (data_gb, DATA_DIR))
