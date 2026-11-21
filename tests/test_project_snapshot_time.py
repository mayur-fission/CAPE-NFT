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
(see common/snapshot_flow.py data_timeout_s / snapshot_timeout_s).

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
import pytest

from common import snapshot_flow as flow
from common.dummy_data import modify_test_data, remove_test_data
from common.snapshot_flow import snapshot_token  # noqa: F401 - the fixture, used via pytestmark
from common.step_log import log_step
from config.config import env, optional_int


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


TEST = "test_project_snapshot_time"
# One full add/modify/remove cycle per size, in this order
DATA_SIZES, DATA_SIZES_PROBLEM = _parse_sizes(env("SNAPSHOT_DATA_GB") or "1")
# Whole test, for the largest size
TEST_TIMEOUT_S = flow.test_timeout_s(max(DATA_SIZES))
# Modify scenario: share of the bytes changed, and the seed picking the files (blank: random, recorded)
MODIFY_PERCENT = float(env("SNAPSHOT_MODIFY_PERCENT") or "10")
MODIFY_SEED = optional_int(env("SNAPSHOT_MODIFY_SEED"))

# usefixtures: the token is asked for (once per run) before the first test opens a browser
pytestmark = [pytest.mark.rstudio_local, pytest.mark.snapshot, pytest.mark.usefixtures("snapshot_token")]


def _check_settings():
    if DATA_SIZES_PROBLEM:
        pytest.fail(DATA_SIZES_PROBLEM)
    if not 0 < MODIFY_PERCENT <= 100:
        pytest.fail("SNAPSHOT_MODIFY_PERCENT is %s: use a share of the data above 0 and up to 100" % MODIFY_PERCENT)
    flow.check_settings(DATA_SIZES)


@pytest.fixture(scope="module", params=DATA_SIZES, ids=lambda gb: "%gGB" % gb)
def data_gb(request) -> float:
    """The data size of this cycle. Module scope makes pytest run every test
    with the first size (add, modify, remove), then every test with the next."""
    log_step("Data size %g GB (cycle %d of %d: %s GB)", request.param, DATA_SIZES.index(request.param) + 1,
             len(DATA_SIZES), ", ".join("%g" % s for s in DATA_SIZES))
    return request.param


def _add_data(page, size_gb: float) -> dict:
    return flow.add_data(page, size_gb)


def _remove_data(page, size_gb: float) -> dict:
    return remove_test_data(page, flow.DATA_DIR, timeout_ms=flow.data_timeout_s(size_gb) * 1000)


def _modify_data(page, size_gb: float) -> dict:
    return modify_test_data(page, flow.DATA_DIR, MODIFY_PERCENT, seed=MODIFY_SEED,
                            timeout_ms=flow.data_timeout_s(size_gb) * 1000)


def _snapshot_after(page, scenario: str, size_gb: float, change_data, what: str):
    _check_settings()
    flow.snapshot_after(page, scenario, size_gb, change_data, what, test=TEST)


@pytest.mark.timeout(TEST_TIMEOUT_S)
def test_snapshot_after_adding_data(data_gb, page):
    """Scenario 1: add data_gb GB of test data, then snapshot."""
    _snapshot_after(page, "add", data_gb, _add_data, "Create %g GB of test data" % data_gb)


@pytest.mark.timeout(TEST_TIMEOUT_S)
def test_snapshot_after_modifying_data(data_gb, page):
    """Scenario 2: change SNAPSHOT_MODIFY_PERCENT % of the test data (data_gb GB) in place, then snapshot."""
    _snapshot_after(page, "modify", data_gb, _modify_data,
                    "Change %g%% of the %g GB of test data in %s" % (MODIFY_PERCENT, data_gb, flow.DATA_DIR))


@pytest.mark.timeout(TEST_TIMEOUT_S)
def test_snapshot_after_removing_data(data_gb, page):
    """Scenario 3: delete the test data (data_gb GB, as added or added and modified), then snapshot."""
    _snapshot_after(page, "remove", data_gb, _remove_data,
                    "Remove the %g GB of test data in %s" % (data_gb, flow.DATA_DIR))
