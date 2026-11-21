"""Initial 50 GB snapshot: how long the first snapshot of a project takes
after a large initial data load - create 50 GB of data in the project, take
a snapshot and time it (finished_at - started_at from the snapshot status
API's response on success). No modify, no remove: the data stays in the
project.

The size is fixed here (SIZE_GB), not read from .env. Everything else - the
project, session, API, token, polling, timeouts, list check and reporting -
is the same as tests/test_project_snapshot_time.py (common/snapshot_flow.py):

Flow: login -> open session SNAPSHOT_SESSION_NAME -> generate SIZE_GB GB in
      DATA_DIR (make_client_shape.py, from the R console) -> API 1 create
      snapshot -> API 2 status every SNAPSHOT_POLL_INTERVAL_S until success
      -> snapshot time -> API 3 snapshot list check -> result to the log,
      Allure, evidence/runs/<run>/snapshot_time_add_50gb.json,
      evidence/snapshot_times.csv (test = test_initial_50gb) and the report
      (evidence/snapshot_report.html / .md / .png)

Data: DATA_DIR = <SNAPSHOT_PROJECT_DIR>/initial_50gb_data - its own folder,
so the add/modify/remove cycle (which deletes SNAPSHOT_DATA_DIR) never
touches it. It must be empty or absent; SNAPSHOT_DATA_CLEAN=1 empties it
first (only a folder holding the generator's marker file is emptied). Each
run uses a new seed (from the run's start time, or SNAPSHOT_DATA_SEED when
set), so data made again after emptying the folder is new to the snapshot.
The server needs 50 GB free in the project folder.

Timeouts: data creation at least 6000 s (120 s per GB), the snapshot at
least 12000 s (240 s per GB); SNAPSHOT_DATA_TIMEOUT_S / SNAPSHOT_TIMEOUT_S
when larger.

Not in the normal suite (pytest.ini deselects the snapshot marker):
    pytest tests/test_initial_50gb.py -m snapshot -s
"""
import posixpath

import pytest

from common import snapshot_flow as flow
from common.snapshot_flow import snapshot_token  # noqa: F401 - the fixture, used via pytestmark

TEST = "test_initial_50gb"
# This test's data size - fixed here, not from .env (SNAPSHOT_DATA_GB is the cycle test's)
SIZE_GB = 50.0
DATA_DIR = posixpath.join(flow.PROJECT_DIR, "initial_50gb_data") if flow.PROJECT_DIR else None
TEST_TIMEOUT_S = flow.test_timeout_s(SIZE_GB)

# usefixtures: the token is asked for (once per run) before the first test opens a browser
pytestmark = [pytest.mark.rstudio_local, pytest.mark.snapshot, pytest.mark.usefixtures("snapshot_token")]


def _create_initial_data(page, size_gb: float) -> dict:
    seed = flow.DATA_SEED if flow.DATA_SEED is not None else flow.run_seed()
    return flow.add_data(page, size_gb, data_dir=DATA_DIR, seed=seed)


@pytest.mark.timeout(TEST_TIMEOUT_S)
def test_initial_50gb_snapshot(page):
    """Create SIZE_GB (50) GB of data in the project, then snapshot it and time it."""
    if not DATA_DIR:
        pytest.fail("set SNAPSHOT_PROJECT_DIR in .env: the 50 GB go to <project dir>/initial_50gb_data")
    flow.check_settings([SIZE_GB])
    flow.snapshot_after(page, "add", SIZE_GB, _create_initial_data,
                        "Create the initial %g GB of data in %s" % (SIZE_GB, DATA_DIR),
                        test=TEST, label="initial", data_dir=DATA_DIR)
