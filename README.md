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
| `R_SCRIPT_HIGH_THROUGHPUT` / `OUTPUT_DIR_HIGH_THROUGHPUT` | test_167, test_168, test_167_multiple_times, test_run_high_throughput_job, test_launch_existing_session_and_run_r_scripts |
| `R_SCRIPT_CLOSE_TAB_JOB` | test_run_r_close_browser |
| `R_SCRIPT_GENERATE_CSV` / `OUTPUT_DIR_GENERATE_CSV` | The CSV-session (group) tests |
| `R_SCRIPT_GROUP_B_WORKBENCH_JOB`, `R_SCRIPT_GROUP_B_BACKGROUND_JOB` | Group B jobs in the group tests |
| `R_SCRIPT_CONCURRENT_SESSIONS` / `OUTPUT_DIR_CONCURRENT_SESSIONS` | test_concurrent_sessions (optional; default to the CSV ones) |
| `R_SCRIPT_CONCURRENT_10MIN`, `R_SCRIPT_CONCURRENT_30MIN` | The 10- and 30-minute concurrent tests |
| `R_SCRIPT_SMALL_10MIN` | `test_concurrent_sessions_10min_s` (`pmx_10kb_10min.R`; the test is skipped when it is not set) |
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
