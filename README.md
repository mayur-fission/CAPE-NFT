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

`.env` is grouped by test file and lists every optional setting with its
default (as a commented-out `#KEY=default` line). Ask a teammate for a copy
rather than starting from scratch.

The R scripts the tests source (`sample_10mb.R`, `sample_100mb.R`,
`generate_10kb_csv.R`, `sample_run_sleep.R`) must already exist on the
server, by default in `/home/posit`.

## Running

The Workbench server must be running. Tests create real sessions on it.

```bash
pytest tests/test_us_167.py -v                 # one file
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
| `test_create_sessions_run_r_close_browser.py` | Scripts and Workbench jobs keep running after the tab closes |
| `test_run_high_throughput_job.py`, `test_us_167.py`, `test_us_168.py` | High-throughput script, timed |
| `test_launch_sessions_api_user1.py` | Create / relaunch sessions through the API only |
| `test_create_session_through_api_and_run_script*.py` | API-created sessions driven in the browser, incl. mixed workloads |
| `test_launch_existing_session_and_run_r_scripts.py` | Reuse existing API sessions |

## Results

Everything a run produces goes to `evidence/` (git-ignored):

- `rstudio_script_timings_<label>.csv` - one row per script run: start, end,
  time taken, status
- `rstudio_*_aggregate_report.csv` / `*_launch_perf.json` - JMeter-style
  launch statistics
- `allure-results/` when run with `--alluredir=evidence/allure-results`

The API tests also write the ids of the sessions they create to
`testdata/session_ids_U<user>.csv` (git-ignored), which later tests reuse.

Metrics are optional. Tests pass without them.

- **OpenTelemetry:** set `OTEL_EXPORTER_OTLP_ENDPOINT` to send to a
  collector. Otherwise metrics print to the console.
- **CloudWatch:** set `RSTUDIO_SERVER_INSTANCE_ID` / `RSTUDIO_DB_INSTANCE_ID`
  to capture server and DB snapshots.
- **Local Prometheus + Grafana:** see `common/observability.py`. It is run by
  hand.

## Layout

```
tests/      test files only: settings, flow and assertions
common/     everything the tests call
config/     report writers (CSV / JSON / Allure)
testdata/   session-name CSVs for the API tests
evidence/   test results (generated)
```

Main modules in `common/`, from low level to high:

| Module | Purpose |
|---|---|
| `config.py` | Loads `.env`; `env()` and shared defaults |
| `locators.py` | Every UI selector and label - update here when the UI changes |
| `rstudio_session_helper.py` | Session list, naming, launch / open / quit |
| `rstudio_console_commands.py` | Run R commands in the console and read results |
| `rstudio_workbench.py`, `rstudio_text_operations.py` | Login, setwd, projects, text files |
| `session_actions.py` | One-step actions with `.env` defaults (login as user N, retries) |
| `script_timings.py` | Run records, waiting on many consoles at once, timings CSV |
| `session_retry.py` | Shared retry / backoff and login staggering |
| `session_scenarios.py`, `session_batch.py`, `session_concurrent.py` | One session, several in turn, several in parallel browsers |
| `rstudio_workbenchjob.py` | Workbench jobs and multi-tab script runs |
| `launch_already_created_sessions.py`, `api_helper.py`, `api_ui_combo.py` | Workbench API and API-created sessions |
| `perf_scenarios.py`, `script_run_scenarios.py`, `helper_function.py` | Complete test flows (cleanup included) and result helpers |
| `otel_metrics.py`, `cloudwatch_metrics.py`, `observability.py` | Optional metrics |

When adding a test, keep it to settings, a flow and assertions. Put reusable
steps in `common/`, and give the test a docstring with a `Flow:` line.
