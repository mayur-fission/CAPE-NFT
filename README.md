# CAPE NFT - RStudio Workbench performance tests

Non-functional tests for Posit Workbench (RStudio Pro). The tests drive a real
browser with Playwright, plus the Workbench API where sessions are created
without one. They measure:

- how long sessions take to launch
- how long R scripts take, alone and with many sessions at once
- Workbench jobs
- idle session stability

Every test cleans up the sessions it creates.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (bash: source .venv/bin/activate)
pip install -r requirements.txt
playwright install chromium
```

For the Allure report a local run builds at the end (optional - without it
the run only saves the Allure results), install Java 8+ and the Allure
command line 2.27 or newer (2.42.0 tested), e.g. on Windows
`scoop install allure`. `allure --version` must work in the shell the tests
run from, or set `ALLURE_CMD` to its path. The report goes to
`evidence/runs/<run start>_allure/allure-report.html`; `LOCAL_ALLURE_REPORT=0`
turns it off. Jenkins builds its own report and needs neither.

Create `.env` in the repo root (it is git-ignored and loaded automatically, in
any shell). At minimum:

```ini
RSTUDIO_BASE_URL=http://<workbench-host>:8787/auth-sign-in
RSTUDIO_USER1=<user>
RSTUDIO_PASSWORD=<password>
API_TOKEN_USER_1=<token>        # only for the API tests
RSTUDIO_WORKING_DIR=/home/<user>
RSTUDIO_SESSION_COUNT=3
```

`.env` holds only the settings in use, grouped (server and users, server
paths, session counts, timings). Other settings are optional - the
code has their defaults - and can be added when needed. Ask a teammate for a
copy rather than starting from scratch.

Server paths have no defaults in the code: every folder, R script and
`source()` command a test uses comes from `.env`, and a test stops with
"<NAME> is not set" when one it needs is missing. They are grouped by name:

| Setting | What it is |
|---|---|
| `WORKBENCH_HOME_DIR` | Home folder of the test user on the server (concurrent tests set it after launch) |
| `RSTUDIO_WORKING_DIR` | Working folder for new sessions (also the API tests) |
| `WORKING_DIR_EXISTING_SESSIONS` | Working folder in existing sessions (optional; defaults to `RSTUDIO_WORKING_DIR`) |
| `R_SCRIPT_HIGH_THROUGHPUT` / `OUTPUT_DIR_HIGH_THROUGHPUT` | test_run_high_throughput_job, test_launch_existing_session_and_run_r_scripts |
| `R_SCRIPT_CLOSE_TAB_JOB` | test_run_r_close_browser |
| `R_SCRIPT_GENERATE_CSV` / `OUTPUT_DIR_GENERATE_CSV` | The CSV-session (group) tests |
| `R_SCRIPT_GROUP_B_WORKBENCH_JOB`, `R_SCRIPT_GROUP_B_BACKGROUND_JOB` | Group B jobs in the group tests |
| `R_SCRIPT_GROUP_B_BACKGROUND_JOB_MULTI_BROWSER` | Group B background job in test_create_session_through_ui_and_run_script_group_multi_browser (`run_until_stopped.R`) |
| `R_SCRIPT_CONCURRENT_SESSIONS` / `OUTPUT_DIR_CONCURRENT_SESSIONS` | test_concurrent_sessions (optional; default to the CSV ones) |
| `R_SCRIPT_CONCURRENT_10MIN`, `R_SCRIPT_CONCURRENT_30MIN` | The 10- and 30-minute concurrent tests |
| `R_SCRIPT_SMALL_10MIN` | `test_concurrent_sessions_10min_s` (`pmx_10kb_10min.R`; the test is skipped when it is not set) |
| `MGSOLVE_SCRIPT` | `test_mgsolve_10min` (`mrgsolve_sim.R`; the test is skipped when it is not set) |
| `PKNCA_SCRIPT` | `test_pknca_10min` (`pknca_nca_10min.R`; the test is skipped when it is not set) |
| `NLMIXR_SCRIPT` | `test_nlmixr_10min` (`nlmixr2_sse_10min.R`; the test is skipped when it is not set) |
| `SHINY_HTML_SCRIPT` | `test_shiny_html_10min` (`test_html_reports_shiny.R`; the test is skipped when it is not set) |
| `TINYVERSE_GGPLOT_SCRIPT` | `test_tidyverse_ggplot_10min` (`tidyverse_ggplot2_10min.R`; the test is skipped when it is not set) |
| `PMT_10MIN_SESSIONS` | `test_pmt_10min`: sessions over the mrgsolve, nlmixr2, PKNCA, tidyverse and `R_SCRIPT_SMALL_10MIN` scripts (multiple of 5; one browser per 5, one tab per script). Missing R packages are installed first (`common/r_packages.py`) |
| `R_SOURCE_COMMAND_NEW_SESSIONS`, `R_SOURCE_COMMAND_EXISTING_SESSIONS` | `source(...)` commands of the perf tests |

The older names (e.g. `HIGH_THROUGHPUT_JOB_SCRIPT`, `CSV_SESSIONS_SCRIPT`)
still work as fallbacks. The scripts must already exist on the server at
those paths.

## Running

The Workbench server must be running. Tests create real sessions on it.

```bash
pytest tests/test_167.py -v                    # one file
pytest -k concurrent -v                        # by name
pytest tests/test_rstudio_sessions.py --headed # watch the browser
pytest --collect-only -q                       # list tests, touches nothing
```

Tests marked `@pytest.mark.skip()` are switched off on purpose. Remove the
marker to run them.

## Tests

Each test's docstring has a `Flow:` line (e.g. `login -> launch new session
-> run script -> quit session`) saying what it does.

| File | What it covers |
|---|---|
| `test_create_and_launch_single_session.py` | One new session reaches its IDE |
| `test_create_and_launch_multiple_sessions*.py` | Several sessions; user 1 / user 2 variants run R scripts in tabs |
| `test_two_user_login.py` | Two users signed in at once |
| `test_rstudio_sessions.py` | Sessions stay alive while held idle |
| `test_rstudio_session_perf.py` | Launch time (single / sequential / concurrent), projects, text files |
| `test_rstudio_run_r_script_perf.py` | R script time in new sessions, one or many users |
| `test_rstudio_source_script_perf.py` | R script time in existing sessions |
| `test_run_r_close_browser.py` | Scripts and Workbench jobs keep running after the tab closes |
| `test_run_r_close_browser_batch_execution.py` | The same in batches over several browsers: each browser starts as soon as its sessions are up, drops are captured, cleanup once all are done |
| `test_run_high_throughput_job.py`, `test_167.py`, `test_168.py` | High-throughput script, timed |
| `test_launch_sessions_api_user1.py` | Create / relaunch sessions through the API only |
| `test_create_session_through_api_and_run_script*.py` | API-created sessions driven in the browser, incl. mixed workloads |
| `test_launch_existing_session_and_run_r_scripts.py` | Reuse existing API sessions |
| `test_project_snapshot_time.py` | Project snapshot time (marker `snapshot`, see below) |

### Snapshot timing

`tests/test_project_snapshot_time.py` first creates test data in an existing
session. It runs `common/make_client_shape.py`, which shapes the files like
the client's project, from the RStudio console:
`python3 make_client_shape.py <N>GB /path/to/empty/folder`. It then triggers
a snapshot of an existing project through the API and times it until the
status is success, waiting at most 2 h. You create the project and its
session by hand first.

It runs only when you ask for it, because `pytest.ini` deselects
`-m snapshot`:

```powershell
pytest tests/test_project_snapshot_time.py -m snapshot -s
```

Settings (in `.env` or the environment):

**Required**
- `SNAPSHOT_PROJECT_DIR`: the project folder on the server, e.g. `/mnt/fsx/CAPE_2026_11_0223`. The project ID comes from its name: `CAPE_2026_11_0223` is project 223.
- `API_BASE_URL`: the snapshot API's base URL, e.g. `https://<host>`.
- `TOKEN`: the snapshot API's bearer token, sent as `Authorization: Bearer <TOKEN>`. Keep it in `.env`, which git ignores; a `Bearer ` prefix is stripped. If it is a JWT, the test fails at the start when it has expired and warns when it would expire during the wait. HTTP 401/403 fails at once.

**Optional**
- `PROJECT_ID`: set it only if the folder name doesn't give the ID. When both are set they must match.
- `SNAPSHOT_DATA_DIR`: the data folder. Defaults to `<SNAPSHOT_PROJECT_DIR>/snapshot_test_data`. It must be empty or not exist yet; its parent must exist.
- `SNAPSHOT_SESSION_NAME`: the project's existing session. Defaults to `TEST_SNAPSHOT_NFR` (`common/session_naming_logic.py`).

**Data**
- `SNAPSHOT_DATA_GB`: decimal GB, default 1. The minimum is 1 and the maximum 1000.
- `SNAPSHOT_DATA_SEED`: the script's seed (its default is 1). The same size and seed always give the same bytes.
- `SNAPSHOT_DATA_CLEAN`: set it to `1` to empty `SNAPSHOT_DATA_DIR` before generating. Without it, a folder that isn't empty fails the test.
- `SNAPSHOT_DATA_SCRIPT`: the local generator script, uploaded before it runs. Default `common/make_client_shape.py`.
- `SNAPSHOT_DATA_SERVER_SCRIPT`: a generator script already on the server, run as is with nothing uploaded.
- `SNAPSHOT_SCRIPT_DIR`: where the script is uploaded. Defaults to `~/snapshot_nfr` in the user's home folder. That keeps the script out of the project, so it is not part of the snapshot, and out of the shared `/mnt/fsx` root, which may not be writable. `~` is allowed.
- The script runs with `python3` (3.9 or later) from the RStudio console: `cd <script folder> && python3 make_client_shape.py <N>GB <data folder>`.
- `SNAPSHOT_DATA_TIMEOUT_S`: default 1800. Raise it for large sizes.

**Polling**
- `SNAPSHOT_POLL_INTERVAL_S`: default 10.
- `SNAPSHOT_TIMEOUT_S`: default 7200.
- `SNAPSHOT_TEST_TIMEOUT_S`: pytest-timeout. Defaults to the data timeout plus the snapshot timeout plus 900 s (9900 s with the defaults).

**API**
- `SNAPSHOT_NAME` (default `NFR <run start>`), `SNAPSHOT_COMMENT` (default `TEST`) and `SNAPSHOT_IS_MAJOR` (default `false`): API 1's body, `POST /api/v1/projects/{project_id}/snapshots`.
- Status (API 2): `GET /api/v1/projects/{project_id}/snapshot-jobs/{snapshot_id}`, polled with the `job_id` from API 1's response. API 1's response looks like `{"job_id": 2389, "project_id": 223, "name": "...", "state": "queued", "status_url": "/api/v1/projects/223/snapshot-jobs/2389"}`. If it includes `status_url`, the test polls that URL instead. The test fails right away if API 1's response shows a different `project_id` or `name` from the ones sent.
- **Snapshot time** is the end time minus the start time in the status response that reports success. It is calculated as `finished_at` − `started_at` (both ISO UTC, for example `2026-11-20T12:39:24.244240Z`). `requested_at` is ignored. `SNAPSHOT_START_FIELD` and `SNAPSHOT_END_FIELD` can name different fields (dotted for nested ones). Each poll logs the state and the step, for example `processing, step cloning (2/6)`. If the job reports a `finished_at` but its state is unknown, the test fails at once rather than waiting for the timeout. The client-side time from the create call to that poll is recorded as `client_duration_s` alongside it.
- Snapshot list (API 3): `GET /api/v1/projects/{project_id}/snapshots?page=1&page_size=20&sort=-snapshot_id`. The entries are in `items`, and the response also has `page`, `page_size` and `total`. After success the test finds our entry by `snapshot_id`, taken from the status response's `snapshot.snapshot_id`. If the status response has no snapshot ID, it searches by name. It then checks that `name`, `comment`, `is_major`, `project_id` and `snapshot_id` match what was sent. `snapshot_size`, `snapshot_version` and `commit_id` are recorded in the result. Up to `SNAPSHOT_LIST_MAX_PAGES` pages (default 5) are read, stopping at `total`. The read is tried `SNAPSHOT_LIST_ATTEMPTS` times (default 6), `SNAPSHOT_LIST_RETRY_S` apart (default 10 s), because the status can report `succeeded` while its `listing` is still `generating`.
- `SNAPSHOT_ID_FIELD`: the field of API 1's response that holds that ID, e.g. `id` or `data.job_id` for a nested one. If it is not set, the first of `job_id`, `jobId`, `snapshot_job_id`, `id`, `snapshot_id`… found is used.
- `SNAPSHOT_TRIGGER_METHOD`, `SNAPSHOT_TRIGGER_PATH`, `SNAPSHOT_STATUS_PATH` and `SNAPSHOT_DETAILS_PATH`: see `common/snapshot_api.py`.

There are three scenarios, which run in this order (`-k adding`, `-k modifying` or `-k removing` runs one of them):
1. **Add:** generate `SNAPSHOT_DATA_GB` of data.
2. **Modify:** overwrite `SNAPSHOT_MODIFY_PERCENT` % of the bytes in place.
3. **Remove:** delete the data folder.

After each scenario, the test writes these files:
- `snapshot_time_<scenario>.json` in the run's evidence folder, attached to Allure. It holds:
  - the snapshot time, with start and end;
  - the queue wait;
  - each poll's state and step, and the time spent in each step;
  - the time from the create call to success;
  - the API responses.
- A row in `evidence/snapshot_times.csv`, one per scenario run, so runs can be compared. A snapshot that fails, times out or doesn't appear correctly in the snapshot list also gets a row, with its outcome and the server's error.
- The report, rebuilt from all runs:
  - `evidence/snapshot_report.html`, attached to Allure;
  - `evidence/snapshot_report.md`.

The report covers:
- an overview and key findings;
- a summary by scenario: minimum, average and maximum time, queue wait, and data changed per minute;
- the latest run;
- every run;
- the time spent in each step;
- the size change shown in the snapshot list;
- the test data;
- failures;
- the method.

To rebuild it without a run:

```
python -m common.snapshot_report
```

## Results

Everything a run produces goes to `evidence/` (git-ignored):

- `rstudio_script_timings_<label>.csv` - one row per script run: start, end,
  time taken, status
- `rstudio_*_aggregate_report.csv` / `*_launch_perf.json` - JMeter-style
  launch statistics
- `allure-results/` when run with `--alluredir=evidence/allure-results`;
  without `--alluredir` (local runs), `runs/<run start>_allure/` with the
  results and a one-file `allure-report.html` built at the end
- `runs/<run start>_<test file>/step_log.txt` (every step, written live) and
  `test_output.txt` (everything each test printed)
- screenshots (also attached to Allure): failures only by default;
  `EVIDENCE_SCREENSHOTS=1` takes one at every step (Jenkins: SCREENSHOT_ALL_STEPS)
- `output_<session>_<file>` - copies of the files the sessions wrote, only with
  `EVIDENCE_TEST_DATA=1` (Jenkins: CAPTURE_TEST_DATA)

The API tests also write the ids of the sessions they create to
`testdata/session_ids_U<user>.csv` (git-ignored), which later tests reuse.

## Layout

```
conftest.py shared pytest fixtures and hooks for the whole suite (project root)
tests/      test files only: settings, flow and assertions
common/     everything the tests call (page helpers, flows, reports)
config/     settings: config.py loads .env, env(), paths, this run's evidence folder
testdata/   session-name CSVs for the API tests
evidence/   test results (generated)
```

Modules in `common/`, from low level to high:

| Layer | Module | Purpose |
|---|---|---|
| Base | `config/config.py` | `.env` settings, option parsing, this run's evidence folder (outside `common/`) |
| | `locators.py` | Every UI selector and label - update here when the UI changes |
| | `step_log.py`, `evidence.py` | Step log lines; screenshots and output copies |
| | `session_naming_logic.py` | Every session name prefix and the name builders |
| | `session_retry.py` | Retry / backoff and login spacing |
| UI building blocks | `rstudio_session_helper.py` | Sign in, session list, launch / open / quit sessions |
| | `rstudio_console_commands.py` | R console: run commands, read results, check files |
| | `rstudio_files.py`, `rstudio_text_operations.py` | Working dir, files, folders, projects; text files |
| | `rstudio_workbenchjob.py`, `rstudio_backgroundjob.py` | Workbench jobs; background jobs |
| | `session_actions.py` | One-step actions with `.env` defaults (login as user N, retries) |
| Flows | `session_scenarios.py`, `session_batch.py`, `session_concurrent.py` | One session; several in turn; several users in parallel |
| | `script_run_scenarios.py`, `script_outputs.py`, `script_timings.py` | R script in several sessions; output checks; run records |
| | `parallel_browsers.py` | Several browsers in threads, the wait for all, stuck-browser capture |
| | `concurrent_sessions.py`, `close_tab_batch.py` | Concurrent / batch tests; close-tab batch test |
| | `session_monitor.py`, `session_cleanup.py` | Session drops and idle soak; end-of-test cleanup |
| | `perf_scenarios.py` | Launch performance runs |
| | `launch_already_created_sessions.py`, `api_helper.py`, `api_ui_combo.py` | Workbench API and API/CSV-created sessions |
| Reports | `result_printing.py`, `session_time_report.py`, `perf_report.py` | Result lines and summaries; session time charts; launch-performance JSON / CSV / Allure |

When adding a test, keep it to settings, a flow and assertions. Put reusable
steps in `common/`, and give the test a docstring with a `Flow:` line.
