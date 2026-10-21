// CAPE NFT - Posit Workbench performance tests.
//
// "Build with Parameters" shows one checkbox per test file; tick any number
// and they run together in a single pytest run, one file after another.
// RUN_IN_PARALLEL instead runs each ticked file in its own pytest at the same
// time (Jenkins parallel branches). RUN_ALL ignores the boxes.
//
// One-time Jenkins setup:
//   - Pipeline job -> "Pipeline script from SCM" -> this repo, script path "Jenkinsfile".
//     Run it once so Jenkins reads the parameters below.
//   - Credentials -> add one "Secret file" per environment, each holding that
//     environment's .env (base URL, users, passwords, API tokens):
//       'cape-nft-env-dev'  (ENVIRONMENT = Dev)
//       'cape-nft-env-qa'   (ENVIRONMENT = QA)
//   - Optional: the Allure Jenkins plugin, for the Allure report link.
//
// Adding a test file: add it to TEST_FILES. Its checkbox appears after the next build.
//
// Several EC2 nodes (tick USE_EC2_NODES, agents in NODE_LABELS): the multi-browser test runs once on each
// labelled agent at the same time, as the same user, with N1_, N2_, ... in
// front of its session names; everything else (e.g. test_active_session_count)
// runs on this build's own agent. Agent setup:
//   - Ubuntu with python3-venv, git, Java 17 and `playwright install-deps chromium` (needs sudo).
//   - Label it (e.g. cape-nft-load-1, cape-nft-load-2), 1 executor, and Usage
//     "Only build jobs with label expressions matching this node", so `agent any`
//     below never takes it (the build would then wait for its own executor).
//   - Clocks NTP-synced (default on EC2): the nodes start at an agreed Unix time.

// Test file (without .py) -> checkbox description, in the order shown in Jenkins.
// No `def`: a script-level `def` is not reliably visible inside pipeline blocks.
TEST_FILES = [
    // --- First, so its checkbox sits right below RUN_ALL
    'test_active_session_count'                         :
        'Count Active + Executing sessions every 10 s. It logs in first, the other ticked tests start once it has ' +
        '(it runs alongside them), and it stops when they all finish or after ACTIVE_SESSIONS_MONITOR_S (20 min)',
    // --- Session basics
    'test_create_and_launch_single_session'             : 'One new session reaches an active IDE; launch time recorded',
    'test_create_and_launch_multiple_sessions'          :
        'SESSION_COUNT new sessions launched one after another each reach an active IDE',
    'test_create_and_launch_multiple_sessions_user1'    :
        'User 1 creates SESSION_COUNT sessions (AUTO_SESSION_U1_<N>) and runs an R script in them',
    'test_create_and_launch_multiple_sessions_user2'    :
        'User 2 creates SESSION_COUNT sessions (AUTO_SESSION_U2_<N>) and runs an R script in them',
    'test_two_user_login'                               :
        'Users 1 and 2 signed in at once, each in its own browser context (creates no sessions)',
    'test_rstudio_sessions'                             : 'Sessions stay alive while idle (file list polled), then are quit',
    // --- High-throughput R script
    'test_167'                                          : 'US 167 - high-throughput script, timed',
    'test_167_multiple_times'                           :
        'US 167 - the test_167 run repeated HTP_REPEAT_RUNS times, HTP_REPEAT_WAIT_S seconds apart',
    'test_168'                                          : 'US 168 - high-throughput script, timed',
    'test_run_high_throughput_job'                      :
        'High-throughput script in new sessions: left running after the tab closes, or timed to completion',
    // --- Sessions left running with their tabs closed
    'test_run_r_close_browser'                          : 'Scripts and Workbench jobs keep running after the tab closes',
    'test_run_r_close_browser_batch_execution'          :
        'Like test_run_r_close_browser, in batches: each browser starts the R script in its sessions as soon as ' +
        'they are launched, closes the tabs and watches for dropped sessions; stop + cleanup once every browser is ' +
        'done',
    // --- Concurrent sessions over several browsers
    'test_concurrent_sessions'                          :
        'CONCURRENT_SESSIONS UI-created sessions (CONCURRENT_SESSION_1, _2, ...) over CONCURRENT_SESSIONS_BROWSERS ' +
        'browsers, R script sourced in all at once and timed, with session time charts',
    'test_concurrent_sessions_u1'                       :
        'Same as test_concurrent_sessions (same CONCURRENT_SESSIONS settings), with sessions named ' +
        'U1_CONCURRENT_SESSION_<N> so both can run at once as the same user',
    'test_concurrent_sessions_batch_execution'          :
        'Like test_concurrent_sessions (same CONCURRENT_SESSIONS settings), but each browser starts the R script ' +
        'as soon as its own sessions are launched; cleanup waits until every browser has finished',
    'test_concurrent_sessions_10min'                    :
        'CONCURRENT_10MIN_SESSIONS UI-created sessions run the ~10 minute R script (htp_10min.R) at once, timed, ' +
        'with charts',
    'test_concurrent_sessions_10min_u1'                 :
        'Same as test_concurrent_sessions_10min (same CONCURRENT_10MIN settings), with sessions named ' +
        'U1_CONCURRENT_10MIN_SESSION_<N> so both can run at once as the same user',
    'test_concurrent_sessions_10min_s'                  :
        'CONCURRENT_SMALL_10MIN_SESSIONS UI-created sessions run the small ~10 minute R script (pmx_10kb_10min.R, ' +
        'R_SCRIPT_SMALL_10MIN in .env) at once, each in its own folder, timed, with charts; all six output files ' +
        '(data/, tables/, logs/) must be written. Skipped while R_SCRIPT_SMALL_10MIN is not set',
    'test_concurrent_sessions_30min'                    :
        'CONCURRENT_30MIN_SESSIONS UI-created sessions run the ~30 minute R script (htp_30min.R) at once, timed, ' +
        'with charts',
    'test_concurrent_sessions_30min_u1'                 :
        'Same as test_concurrent_sessions_30min (same CONCURRENT_30MIN settings), with sessions named ' +
        'U1_CONCURRENT_30MIN_SESSION_<N> so both can run at once as the same user',
    // --- UI-created sessions with mixed workloads
    'test_create_session_through_ui_and_run_script_group' :
        'UI-created sessions, mixed workloads',
    'test_create_session_through_ui_and_run_script_group_multi_browser' :
        'UI-created sessions, mixed workloads, in UI_BROWSERS browsers launching in parallel (B1_, B2_, ... name ' +
        'prefixes)',
    // --- Workbench API
    'test_launch_sessions_api_user1'                    : 'Create / relaunch sessions through the API only',
    'test_create_session_through_api_and_run_script'    : 'API-created sessions driven in the browser',
    'test_create_session_through_api_and_run_script_group' :
        'API-created sessions, mixed workloads',
    'test_launch_existing_session_and_run_r_scripts'    : 'Reuse existing API sessions',
    // --- Performance (JSON / CSV / Allure reports)
    'test_rstudio_session_perf'                         :
        'Session launch time (single / sequential / concurrent) and launch + project + text-file scenarios',
    'test_rstudio_run_r_script_perf'                    :
        'R script run time in new Run_R_session_<N> sessions, once or RUN_COUNT times, one or several users',
    'test_rstudio_source_script_perf'                   : 'Large R script sourced in already-existing session(s), timed',
    // --- Housekeeping - last
    'test_delete_sessions'                              :
        'Housekeeping: quit every AUTO_PERF_SESSION_* session (bulk Quit) - also ones another running test is ' +
        'using',
]
TEST_NAMES = new ArrayList(TEST_FILES.keySet())
// Copies of other tests under U1_ session names, for running next to them as
// the same user: only when ticked, never with RUN_ALL (that would double the
// concurrent-session load).
RUN_ALL_SKIPS = ['test_concurrent_sessions_u1', 'test_concurrent_sessions_10min_u1',
                 'test_concurrent_sessions_30min_u1']
RUN_ALL_TESTS = TEST_NAMES.findAll { !(it in RUN_ALL_SKIPS) }
// The test USE_EC2_NODES spreads over the NODE_LABELS agents.
MULTI_NODE_TEST = 'test_create_session_through_ui_and_run_script_group_multi_browser'
// Written when every other parallel test has finished; test_active_session_count
// stops counting once it appears.
STOP_FILE = 'evidence/.active_sessions_stop'
// Written by test_active_session_count once it is logged in; the other parallel
// tests wait for it, so counting starts before their browsers log in and launch.
READY_FILE = 'evidence/.active_sessions_ready'

// Job parameters and settings, in display order. Set with properties() rather
// than a declarative parameters {} block because properties() REPLACES the
// job's whole parameter list on every build: a stale parameter (e.g. the old
// ENV dropdown) or one added by hand in the Jenkins UI is removed, and the
// order always matches this list.
def jobParams = []
// Descriptions: what it does | values | default | which tests use it.
// --- Environment ---
jobParams << choice(name: 'ENVIRONMENT', choices: ['Dev', 'QA'],
    description: '[Environment] Workbench server the tests run against. Dev uses the secret file cape-nft-env-dev, ' +
                 'QA uses cape-nft-env-qa (base URL, users, passwords, API tokens). Used by: every test')
// --- EC2 nodes ---
jobParams << booleanParam(name: 'USE_EC2_NODES', defaultValue: false,
    description: '[EC2 nodes] Ticked: test_create_session_through_ui_and_run_script_group_multi_browser runs once on each ' +
                 'agent in NODE_LABELS, all at the same time, as the same user; every other ticked test still runs on ' +
                 'this build\'s agent. That test must be ticked too, or the build stops. Unticked (default): everything ' +
                 'runs on this build\'s agent and NODE_LABELS / NODE_START_OFFSET_S are ignored')
jobParams << string(name: 'NODE_LABELS', defaultValue: 'cape-nft-load-1,cape-nft-load-2',
    description: '[EC2 nodes] With USE_EC2_NODES: Jenkins agent labels, separated by commas (must not be empty). Agent 1 ' +
                 'prefixes its session names with N1_, agent 2 with N2_, and so on. Sessions = agents x UI_BROWSERS x 5, ' +
                 'e.g. 2 labels x 4 browsers = 40. Default: cape-nft-load-1,cape-nft-load-2')
jobParams << string(name: 'NODE_START_OFFSET_S', defaultValue: '30',
    description: '[EC2 nodes] With USE_EC2_NODES: seconds between one agent starting its browsers and the next (agent 1 at ' +
                 '0 s, agent 2 at this, agent 3 at twice this...), so their logins do not collide. Whole number, 0 or more. ' +
                 'Default: 30. Keep UI_BROWSER_STAGGER_S at least agents x this (2 agents: 30 here, 60 there)')
// --- Browser and evidence ---
jobParams << booleanParam(name: 'HEADED', defaultValue: false,
    description: '[Browser and evidence] Ticked: browsers open with windows (pytest --headed). On a Linux agent they open on a ' +
                 'virtual 1920x1080 screen (needs xvfb installed there, else the build stops); on a Windows agent on its ' +
                 'desktop. Uses more memory per tab. Unticked (default): headless')
jobParams << booleanParam(name: 'SCREENSHOT_ALL_STEPS', defaultValue: false,
    description: '[Browser and evidence] Ticked: a screenshot at every step (session opened, script started, script ' +
                 'finished...), attached to Allure. Unticked (default): screenshots only when something fails. Reports, ' +
                 'timing CSVs and charts are produced either way. Adds time and disk use on large runs')
jobParams << booleanParam(name: 'CAPTURE_TEST_DATA', defaultValue: false,
    description: '[Browser and evidence] Ticked: keep the data the run creates - copies of the output files the sessions ' +
                 'write (evidence/output_*, files up to 200 KB) and the created session id lists ' +
                 '(testdata/session_ids_U<user>.csv), attached to Allure and archived with the build. Unticked (default): ' +
                 'not kept (the id lists are still written for later tests, just not archived)')
jobParams << booleanParam(name: 'DOWNLOAD_ALLURE_REPORT', defaultValue: true,
    description: '[Browser and evidence] Ticked (default): the Allure report is also saved as one self-contained HTML file ' +
                 '(Build Artifacts -> allure-report.zip: click to download, unzip, open allure-report.html in any browser), ' +
                 'no Jenkins needed (the pipeline downloads the Allure command line for it). Unticked: only the ' +
                 'Allure report inside Jenkins')
// --- How tests run ---
jobParams << booleanParam(name: 'RUN_IN_PARALLEL', defaultValue: false,
    description: '[How tests run] Ticked: the ticked test files start at the same time, each in its own pytest (one ' +
                 'Jenkins parallel branch per file). Only for tests that keep to their own sessions (e.g. test_167 + ' +
                 'test_168); tests that quit all automation sessions or use the same session names interfere with each ' +
                 'other. Unticked (default): the files run one after another in a single pytest')
jobParams << string(name: 'PARALLEL_LOGIN_GAP_S', defaultValue: '30',
    description: '[How tests run] With RUN_IN_PARALLEL: minimum seconds between any two logins across all the parallel ' +
                 'tests, since Workbench fails sign-ins that happen at the same time. Whole number, 0 or more. Default: 30. ' +
                 'Ignored when RUN_IN_PARALLEL is unticked')
jobParams << booleanParam(name: 'RUN_ALL', defaultValue: false,
    description: '[How tests run] Ticked: run every test file below (except the _u1 copies), whatever is ticked. ' +
                 'Unticked (default): only the ticked files run (at least one is required)')
// --- Tests to run (one checkbox per file) ---
for (name in TEST_NAMES) {
    jobParams << booleanParam(name: name, defaultValue: false, description: TEST_FILES[name])
}
// --- Shared by several tests ---
jobParams << string(name: 'SESSION_COUNT', defaultValue: '10',
    description: '[Shared] Sessions each multi-session test launches (sets RSTUDIO_SESSION_COUNT, overriding .env). Whole ' +
                 'number, at least 1. Default: 10. Used by: test_168 and ' +
                 'test_run_r_close_browser. Not used by the concurrent-session tests (see below)')
jobParams << string(name: 'UI_BROWSER_STAGGER_S', defaultValue: '30',
    description: '[Shared] Seconds between starting one browser and the next within a test, so their logins do not ' +
                 'collide. Whole number, 0 or more. Default: 30. A run with N browsers takes (N-1) x this to get them all ' +
                 'started. Used by: the multi-browser group test and test_concurrent_sessions, _10min, _30min')
jobParams << string(name: 'GROUP_TEST_DURATION_S', defaultValue: '180',
    description: '[Shared] How long the mixed workloads keep running in the group tests, in seconds. Whole number. ' +
                 'Default: 180 (3 min). Used by: test_create_session_through_api_and_run_script_group, ' +
                 'test_create_session_through_ui_and_run_script_group and its _multi_browser version')
// --- Multi-browser test ---
jobParams << string(name: 'UI_BROWSERS', defaultValue: '2',
    description: '[Multi-browser test] Browsers test_create_session_through_ui_and_run_script_group_multi_browser opens ' +
                 '(B1_, B2_... name prefixes). Each opens one tab per session name in the group CSVs (5 today), so ' +
                 'sessions = this x 5 (2 = 10 sessions). Whole number, at least 1. Default: 2. With USE_EC2_NODES this is ' +
                 'per agent. About 4-6 browsers fit on a 16 GB agent')
// --- Concurrent session tests ---
jobParams << string(name: 'CONCURRENT_SESSIONS', defaultValue: '10',
    description: '[Concurrent session tests] test_concurrent_sessions: sessions (CONCURRENT_SESSION_1, _2...) launched from ' +
                 'the UI that all source the short R script (generate_10kb_csv.R) at the same moment, each timed. Whole ' +
                 'number, at least 1. Default: 10')
jobParams << string(name: 'CONCURRENT_SESSIONS_BROWSERS', defaultValue: '2',
    description: '[Concurrent session tests] test_concurrent_sessions only: browsers the CONCURRENT_SESSIONS sessions are ' +
                 'split over, one tab per session. Keep it to 5-6 tabs per browser (10 sessions: 2, 30 sessions: 5). Blank = ' +
                 'one browser per 5 sessions. Default: 2. The _10min and _30min tests always use one browser per 5 sessions')
jobParams << string(name: 'CONCURRENT_10MIN_SESSIONS', defaultValue: '1',
    description: '[Concurrent session tests] test_concurrent_sessions_10min: sessions that all source the ~10 minute R script ' +
                 '(htp_10min.R) at the same moment, each timed (limit 15 min). One browser per 5 sessions. Whole number, ' +
                 'at least 1. Default: 1')
jobParams << string(name: 'CONCURRENT_SMALL_10MIN_SESSIONS', defaultValue: '1',
    description: '[Concurrent session tests] test_concurrent_sessions_10min_s: sessions that all source the small ~10 ' +
                 'minute R script (pmx_10kb_10min.R) at the same moment, each timed (limit 15 min). One browser per 5 ' +
                 'sessions. Whole number, at least 1. Default: 1')
jobParams << string(name: 'CONCURRENT_30MIN_SESSIONS', defaultValue: '1',
    description: '[Concurrent session tests] test_concurrent_sessions_30min: sessions that all source the ~30 minute R script ' +
                 '(htp_30min.R) at the same moment, each timed (limit 40 min). One browser per 5 sessions. Whole number, ' +
                 'at least 1. Default: 1. On the 16 GB Jenkins host about 15 is the safe maximum (25 crashed browser tabs)')
jobParams << string(name: 'SESSION_TIME_THRESHOLD_S', defaultValue: '',
    description: '[Concurrent session tests] Slow-session limit in seconds (decimals allowed, e.g. 650): drawn as a line on ' +
                 'the session time charts, with a count of the sessions above it. It only marks the charts and does not ' +
                 'fail the test. Blank (default) = no line. Used by: test_concurrent_sessions, _10min, _10min_s, _30min')
// --- Other tests ---
jobParams << string(name: 'HIGH_THROUGHPUT_DURATION_S', defaultValue: '',
    description: '[Other tests] test_167 / test_168: seconds each high-throughput script run may take before it counts as ' +
                 'failed. Whole number, at least 1. Blank (default) = HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS from .env ' +
                 '(180 if not set there)')
jobParams << string(name: 'HTP_REPEAT_RUNS', defaultValue: '3',
    description: '[Other tests] test_167_multiple_times: how many times the test_167 run is repeated, one after another ' +
                 '(a new session each time). Whole number, at least 1. Default: 3')
jobParams << string(name: 'HTP_REPEAT_WAIT_S', defaultValue: '30',
    description: '[Other tests] test_167_multiple_times: seconds to wait before each run after the first. ' +
                 'Whole number, 0 or more. Default: 30')
jobParams << string(name: 'CLOSE_BROWSER_WAIT_S', defaultValue: '',
    description: '[Other tests] test_run_r_close_browser: seconds the sessions keep running with their tabs ' +
                 'closed before they are quit. Whole number. Blank (default) = RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS ' +
                 'from .env (300 if not set there)')
// --- Active session count ---
jobParams << string(name: 'ACTIVE_SESSIONS_MONITOR_S', defaultValue: '1200',
    description: '[Active session count] test_active_session_count: the longest it keeps counting Active + Executing ' +
                 'sessions (every 10 s), in seconds. Whole number. Default: 1200 (20 min). Ticked with other tests, it ' +
                 'stops as soon as they have all finished; ticked on its own, it counts for this long')
jobParams << string(name: 'ACTIVE_SESSIONS_USERS', defaultValue: '1',
    description: '[Active session count] test_active_session_count: user numbers whose sessions are counted and added up, ' +
                 'separated by commas (e.g. 1 or 1,2). The users come from .env. Default: 1')
// --- pytest ---
jobParams << string(name: 'PYTEST_K', defaultValue: '',
    description: '[pytest] Optional: pytest -k expression that picks tests inside the ticked files by name (e.g. ' +
                 '"concurrent", or "single and not perf"). Blank (default) = every test in the ticked files')
jobParams << string(name: 'EXTRA_PYTEST_ARGS', defaultValue: '',
    description: '[pytest] Optional: extra arguments added to the pytest command, e.g. "-x" (stop at the first failure) or ' +
                 '"--maxfail=2". Blank (default) = none')

// --- Email ---
jobParams << string(name: 'EMAIL_TO', defaultValue: 'mayur.kumar@fissionlabs.com',
    description: '[Email] Optional: addresses that get the result email when the build ends (pass or fail), separated by ' +
                 'commas, e.g. "a@company.com, b@company.com". The email has the status, the tests run, links to the ' +
                 'Allure report and the console, and allure-report.html attached (if DOWNLOAD_ALLURE_REPORT is ticked). ' +
                 'Blank = no email. Needs the Email Extension plugin with SMTP set up (Manage Jenkins -> System)')

properties([
    parameters(jobParams),
    buildDiscarder(logRotator(numToKeepStr: '30')),
    disableConcurrentBuilds(),   // tests create real sessions on one server
])

// Run a shell command on Linux/macOS or Windows agents alike.
def run(String cmd) {
    if (isUnix()) {
        sh cmd
    } else {
        bat cmd
    }
}

def runStatus(String cmd) {
    return isUnix() ? sh(script: cmd, returnStatus: true) : bat(script: cmd, returnStatus: true)
}

// Create .venv with the requirements and Playwright's Chromium in the current workspace.
def setupPython() {
    if (isUnix()) {
        sh '''
            set -e
            python3 -m venv .venv
            .venv/bin/python -m pip install --upgrade pip
            .venv/bin/python -m pip install -r requirements.txt
            .venv/bin/python -m playwright install chromium
        '''
    } else {
        // Each command is checked, so the build stops at the exact
        // line that fails (a bat step only reports the LAST exit code).
        bat '''
            @echo on
            python --version
            if errorlevel 1 exit /b 1

            python -m venv .venv
            if errorlevel 1 exit /b 1

            .venv\\Scripts\\python.exe -m pip install --upgrade pip
            if errorlevel 1 exit /b 1

            .venv\\Scripts\\python.exe -m pip install -r requirements.txt
            if errorlevel 1 exit /b 1

            .venv\\Scripts\\python.exe -m playwright install chromium
            if errorlevel 1 exit /b 1
        '''
    }
}

// Copy the ENVIRONMENT's .env into the workspace (the tests load it from the
// repo root, config/config.py) and drop the last build's results, since the
// workspace is reused between builds. Call inside withCredentials(ENV_FILE).
// SESSION_COUNT, HIGH_THROUGHPUT_DURATION_S and the evidence switches
// (SCREENSHOT_ALL_STEPS, CAPTURE_TEST_DATA) are appended to .env rather than
// passed with withEnv, because .env is loaded with override=True and
// python-dotenv keeps the last value.
def prepareWorkspace() {
    def overrides = [
        "RSTUDIO_SESSION_COUNT=${(params.SESSION_COUNT ?: '10').trim()}",
        "EVIDENCE_SCREENSHOTS=${params.SCREENSHOT_ALL_STEPS?.toString() == 'true' ? '1' : '0'}",
        "EVIDENCE_TEST_DATA=${params.CAPTURE_TEST_DATA?.toString() == 'true' ? '1' : '0'}",
    ]
    def htpDuration = (params.HIGH_THROUGHPUT_DURATION_S ?: '').trim()
    if (htpDuration) {
        overrides << "HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS=${htpDuration}"
    }
    if (isUnix()) {
        // Jenkins keeps secret files read-only and cp keeps that mode: make the
        // copy writable, since the overrides below are appended to it.
        sh 'rm -f .env && cp "$ENV_FILE" .env && chmod 600 .env'
        sh "printf '\\n%s\\n' ${overrides.collect { "'${it}'" }.join(' ')} >> .env"
        sh "rm -rf evidence/runs evidence/junit*.xml evidence/*.png evidence/output_* evidence/allure-results evidence/.login_slot.lock ${STOP_FILE} ${READY_FILE}"
    } else {
        bat '(if exist .env del /F /Q .env) && copy /Y "%ENV_FILE%" .env && attrib -R .env'
        bat "(echo.${overrides.collect { "& echo ${it}" }.join('')})>> .env"
        bat '''
            if exist evidence\\junit*.xml del /F /Q evidence\\junit*.xml
            if exist evidence\\*.png del /F /Q evidence\\*.png
            if exist evidence\\output_* del /F /Q evidence\\output_*
            if exist evidence\\.login_slot.lock del /F /Q evidence\\.login_slot.lock
            if exist evidence\\.active_sessions_stop del /F /Q evidence\\.active_sessions_stop
            if exist evidence\\.active_sessions_ready del /F /Q evidence\\.active_sessions_ready
            if exist evidence\\allure-results rmdir /S /Q evidence\\allure-results
            if exist evidence\\runs rmdir /S /Q evidence\\runs
            exit /b 0
        '''
    }
}

// Allure command line used for allure-report.html: downloaded once from Maven
// Central into the workspace (.allure-cli/), so the report does not depend on
// the version of the Jenkins 'allure' tool (--single-file needs 2.27+).
ALLURE_CLI_VERSION = '2.34.0'

// Builds the Allure report as one self-contained allure-report.html (opens from
// disk, no web server needed) and archives it for download (Build Artifacts).
// Needs Java and curl on the agent. Never fails the build.
def downloadableAllureReport() {
    if (!fileExists('evidence/allure-results')) {
        echo 'No Allure results - no downloadable Allure report'
        return
    }
    def v = ALLURE_CLI_VERSION
    def url = "https://repo1.maven.org/maven2/io/qameta/allure/allure-commandline/${v}/allure-commandline-${v}.zip"
    // Any Python unzips it (python -m zipfile); the venv's if the build got that far.
    def py = isUnix() ? (fileExists('.venv/bin/python') ? '.venv/bin/python' : 'python3')
                      : (fileExists('.venv\\Scripts\\python.exe') ? '.venv\\Scripts\\python.exe' : 'python')
    try {
        if (isUnix()) {
            sh """
                set -e
                if [ ! -f .allure-cli/allure-${v}/bin/allure ]; then
                    rm -rf .allure-cli && mkdir -p .allure-cli
                    curl -fsSL -o .allure-cli/allure.zip "${url}"
                    ${py} -m zipfile -e .allure-cli/allure.zip .allure-cli
                fi
                rm -rf allure-report-single allure-report.html allure-report.zip
                # zipfile drops the exec bit, so run the launcher through sh
                sh .allure-cli/allure-${v}/bin/allure generate --single-file evidence/allure-results --clean -o allure-report-single
                mv allure-report-single/index.html allure-report.html
                ${py} -m zipfile -c allure-report.zip allure-report.html
            """
        } else {
            bat """
                if not exist .allure-cli\\allure-${v}\\bin\\allure.bat (
                    if exist .allure-cli rmdir /S /Q .allure-cli
                    mkdir .allure-cli
                    curl.exe -fsSL -o .allure-cli\\allure.zip "${url}" || exit /b 1
                    ${py} -m zipfile -e .allure-cli\\allure.zip .allure-cli || exit /b 1
                )
                if exist allure-report-single rmdir /S /Q allure-report-single
                if exist allure-report.html del /F /Q allure-report.html
                if exist allure-report.zip del /F /Q allure-report.zip
                call .allure-cli\\allure-${v}\\bin\\allure.bat generate --single-file evidence\\allure-results --clean -o allure-report-single || exit /b 1
                move /Y allure-report-single\\index.html allure-report.html || exit /b 1
                ${py} -m zipfile -c allure-report.zip allure-report.html
            """
        }
        // Jenkins serves artifacts with a CSP that blocks scripts, so clicking the
        // .html shows a blank page; the .zip downloads on click instead.
        archiveArtifacts artifacts: 'allure-report.html, allure-report.zip', fingerprint: true
        echo 'Downloadable Allure report: Build Artifacts -> allure-report.zip (unzip, open allure-report.html)'
    } catch (Exception e) {
        echo "Downloadable Allure report not built: ${e.message}"
    }
}

// Emails the build result to EMAIL_TO (Email Extension plugin, SMTP set up in
// Manage Jenkins -> System -> Extended E-mail Notification), with
// allure-report.html attached when it was built. Never fails the build.
def sendResultEmail() {
    def to = (params.EMAIL_TO ?: '').trim()
    if (!to) {
        echo 'EMAIL_TO is blank - no result email'
        return
    }
    def status = currentBuild.currentResult
    def tests = (env.SELECTED_TESTS ?: '').split(',').findAll { it }
    def hasReport = fileExists('allure-report.zip')
    def body = """\
        <p><b>${env.JOB_NAME} #${env.BUILD_NUMBER}: ${status}</b>
           (${params.ENVIRONMENT}, took ${currentBuild.durationString.replace(' and counting', '')})</p>
        <p>Tests run:<br>${tests ? tests.join('<br>') : '(none - the build stopped before the tests)'}</p>
        <p><a href="${env.BUILD_URL}allure/">Allure report</a> |
           <a href="${env.BUILD_URL}testReport/">Test results</a> |
           <a href="${env.BUILD_URL}console">Console log</a> |
           <a href="${env.BUILD_URL}artifact/">Build artifacts</a></p>
        <p>${hasReport ? 'The full Allure report is attached (allure-report.zip) - unzip it and open allure-report.html in any browser.'
                       : 'No Allure report attached this time (DOWNLOAD_ALLURE_REPORT unticked or the report could not be built).'}</p>
    """.stripIndent()
    try {
        emailext(
            to: to,
            subject: "[${status}] ${env.JOB_NAME} #${env.BUILD_NUMBER} - ${params.ENVIRONMENT} test report",
            body: body,
            mimeType: 'text/html',
            attachmentsPattern: hasReport ? 'allure-report.zip' : '',
        )
        echo "Result email sent to ${to}"
    } catch (NoSuchMethodError ignored) {
        echo 'Email Extension plugin not installed - no result email'
    } catch (Exception e) {
        echo "Result email not sent: ${e.message}"
    }
}

// The command that starts pytest with the agent's Python (pyPath). HEADED on a
// Linux agent runs it under Xvfb, a virtual screen, since a server has no
// display; BROWSER_WINDOW_SIZE sizes the windows there (config/config.py).
def pytestCommand(String pyPath) {
    if (params.HEADED?.toString() == 'true' && isUnix()) {
        if (sh(script: 'command -v xvfb-run', returnStatus: true) != 0) {
            error("HEADED needs Xvfb on agent ${env.NODE_NAME}: install it there (apt-get install -y xvfb xauth), or untick HEADED")
        }
        return "BROWSER_WINDOW_SIZE=1920,1080 xvfb-run -a --server-args='-screen 0 1920x1080x24' ${pyPath} -m pytest"
    }
    return "${pyPath} -m pytest"
}

// .env holds credentials; never leave it in a workspace.
def removeEnvFile() {
    run(isUnix() ? 'rm -f .env' : 'if exist .env del /F /Q .env')
}

pipeline {
    agent any

    options {
        skipDefaultCheckout()       // checked out in the Checkout stage, after the selection check
    }

    environment {
        PYTHONUNBUFFERED = '1'
        PYTHONUTF8 = '1'
        PYTHONIOENCODING = 'utf-8'
        // '0' = install Playwright browsers inside the venv (site-packages), so the
        // Jenkins service account's user profile folder is not needed.
        PLAYWRIGHT_BROWSERS_PATH = '0'
    }

    stages {
        stage('Select tests') {
            steps {
                script {
                    def selected = params.RUN_ALL ? RUN_ALL_TESTS : TEST_NAMES.findAll { params[it] }
                    // A field added since the last build has no value yet: skip it, its default applies.
                    if (params.ACTIVE_SESSIONS_USERS != null && !(params.ACTIVE_SESSIONS_USERS.trim() ==~ /\d+(\s*,\s*\d+)*/)) {
                        error("ACTIVE_SESSIONS_USERS must be user numbers separated by commas (e.g. 1,2), got '${params.ACTIVE_SESSIONS_USERS}'")
                    }
                    if (params.CLOSE_BROWSER_WAIT_S?.trim() && !(params.CLOSE_BROWSER_WAIT_S.trim() ==~ /\d+/)) {
                        error("CLOSE_BROWSER_WAIT_S must be a whole number of seconds or blank, got '${params.CLOSE_BROWSER_WAIT_S}'")
                    }
                    if (!selected) {
                        error('No tests selected. Tick at least one test checkbox (or RUN_ALL) and build again.')
                    }
                    // Agents the multi-browser test runs on; empty = this build's agent.
                    def nodeLabels = params.USE_EC2_NODES?.toString() == 'true'
                        ? (params.NODE_LABELS ?: '').split(',').collect { it.trim() }.findAll { it } : []
                    if (params.USE_EC2_NODES?.toString() == 'true') {
                        if (!nodeLabels) {
                            error('USE_EC2_NODES is ticked but NODE_LABELS is empty - give the agent labels, or untick USE_EC2_NODES')
                        }
                        if (!selected.contains(MULTI_NODE_TEST)) {
                            error("USE_EC2_NODES only spreads ${MULTI_NODE_TEST} over agents - tick it too, or untick USE_EC2_NODES")
                        }
                    }
                    env.RUN_NODE_LABELS = nodeLabels.join(',')
                    def concurrentBrowsers = (params.CONCURRENT_SESSIONS_BROWSERS ?: '').trim()
                    if (concurrentBrowsers && (!(concurrentBrowsers ==~ /\d+/) || concurrentBrowsers.toInteger() < 1)) {
                        error("CONCURRENT_SESSIONS_BROWSERS must be blank or a whole number of at least 1, got '${params.CONCURRENT_SESSIONS_BROWSERS}'")
                    }
                    def threshold = (params.SESSION_TIME_THRESHOLD_S ?: '').trim()
                    if (threshold && !(threshold ==~ /\d+(\.\d+)?/)) {
                        error("SESSION_TIME_THRESHOLD_S must be blank or a number of seconds, got '${params.SESSION_TIME_THRESHOLD_S}'")
                    }
                    def atLeastOne = ['UI_BROWSERS', 'SESSION_COUNT', 'CONCURRENT_SESSIONS', 'CONCURRENT_10MIN_SESSIONS', 'CONCURRENT_30MIN_SESSIONS',
                                      'CONCURRENT_SMALL_10MIN_SESSIONS',
                                      'HTP_REPEAT_RUNS']
                    ['UI_BROWSERS', 'SESSION_COUNT', 'CONCURRENT_SESSIONS', 'CONCURRENT_10MIN_SESSIONS', 'CONCURRENT_30MIN_SESSIONS',
                     'CONCURRENT_SMALL_10MIN_SESSIONS',
                     'HTP_REPEAT_RUNS', 'HTP_REPEAT_WAIT_S',
                     'UI_BROWSER_STAGGER_S', 'PARALLEL_LOGIN_GAP_S', 'GROUP_TEST_DURATION_S',
                     'ACTIVE_SESSIONS_MONITOR_S', 'NODE_START_OFFSET_S'].each { name ->
                        if (params[name] == null) {
                            return
                        }
                        def value = params[name]?.trim()
                        if (!(value ==~ /\d+/) || (name in atLeastOne && value.toInteger() < 1)) {
                            error("${name} must be a whole number${name in atLeastOne ? ' of at least 1' : ''}, got '${params[name]}'")
                        }
                    }
                    def htpDuration = (params.HIGH_THROUGHPUT_DURATION_S ?: '').trim()
                    if (htpDuration && (!(htpDuration ==~ /\d+/) || htpDuration.toInteger() < 1)) {
                        error("HIGH_THROUGHPUT_DURATION_S must be blank or a whole number of at least 1, got '${params.HIGH_THROUGHPUT_DURATION_S}'")
                    }
                    env.TEST_PATHS = selected.collect { "tests/${it}.py" }.join(' ')
                    env.SELECTED_TESTS = selected.join(',')
                    currentBuild.description = "${params.ENVIRONMENT}: " +
                        (params.RUN_ALL ? 'all tests' : selected.join(', ')) +
                        (nodeLabels ? " (on ${nodeLabels.size()} nodes)" : '')
                    echo "Running ${selected.size()} test file(s) on ${params.ENVIRONMENT}" +
                         (params.RUN_IN_PARALLEL && selected.size() > 1 ? ' in parallel' : '') +
                         ":\n  " + selected.join('\n  ') +
                         (nodeLabels ? "\n${MULTI_NODE_TEST} runs on the agents: ${nodeLabels.join(', ')}"
                                     : '\nUSE_EC2_NODES off - everything runs on this agent')
                }
            }
        }

        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Set up Python') {
            steps {
                script { setupPython() }
            }
        }

        stage('Run tests') {
            steps {
                // The tests load .env from the repo root (config/config.py).
                // Dev -> 'cape-nft-env-dev', QA -> 'cape-nft-env-qa'.
                // Settings for the group tests; .env overrides them if it sets the same names.
                withEnv([
                    "GROUP_SESSIONS_UI_BROWSERS=${params.UI_BROWSERS.trim()}",
                    "GROUP_SESSIONS_UI_BROWSER_STAGGER_S=${params.UI_BROWSER_STAGGER_S.trim()}",
                    "GROUP_SESSIONS_TEST_DURATION_S=${params.GROUP_TEST_DURATION_S.trim()}",
                    "CLOSE_BROWSER_WAIT_S=${(params.CLOSE_BROWSER_WAIT_S ?: '').trim()}",
                    "ACTIVE_SESSIONS_MONITOR_S=${(params.ACTIVE_SESSIONS_MONITOR_S ?: '1200').trim()}",
                    "ACTIVE_SESSIONS_USERS=${(params.ACTIVE_SESSIONS_USERS ?: '1').trim()}",
                    "CONCURRENT_SESSIONS=${(params.CONCURRENT_SESSIONS ?: '10').trim()}",
                    "CONCURRENT_SESSIONS_BROWSERS=${(params.CONCURRENT_SESSIONS_BROWSERS ?: '').trim()}",
                    "CONCURRENT_SESSIONS_BROWSER_STAGGER_S=${params.UI_BROWSER_STAGGER_S.trim()}",
                    "CONCURRENT_10MIN_SESSIONS=${(params.CONCURRENT_10MIN_SESSIONS ?: '1').trim()}",
                    "CONCURRENT_30MIN_SESSIONS=${(params.CONCURRENT_30MIN_SESSIONS ?: '1').trim()}",
                    "CONCURRENT_SMALL_10MIN_SESSIONS=${(params.CONCURRENT_SMALL_10MIN_SESSIONS ?: '1').trim()}",
                    "CONCURRENT_SESSIONS_THRESHOLD_S=${(params.SESSION_TIME_THRESHOLD_S ?: '').trim()}",
                    "CONCURRENT_10MIN_THRESHOLD_S=${(params.SESSION_TIME_THRESHOLD_S ?: '').trim()}",
                    "CONCURRENT_30MIN_THRESHOLD_S=${(params.SESSION_TIME_THRESHOLD_S ?: '').trim()}",
                    "CONCURRENT_SMALL_10MIN_THRESHOLD_S=${(params.SESSION_TIME_THRESHOLD_S ?: '').trim()}",
                    "HTP_REPEAT_RUNS=${(params.HTP_REPEAT_RUNS ?: '3').trim()}",
                    "HTP_REPEAT_WAIT_S=${(params.HTP_REPEAT_WAIT_S ?: '30').trim()}",
                ]) {
                    withCredentials([file(credentialsId: "cape-nft-env-${params.ENVIRONMENT.toLowerCase()}", variable: 'ENV_FILE')]) {
                        script {
                            prepareWorkspace()

                            def kArg = params.PYTEST_K?.trim() ? "-k \"${params.PYTEST_K.trim()}\"" : ''
                            def commonArgs = "-v ${kArg} ${params.HEADED?.toString() == 'true' ? '--headed' : ''} --screenshot only-on-failure ${params.EXTRA_PYTEST_ARGS ?: ''} --alluredir=evidence/allure-results"
                            def py = isUnix() ? '.venv/bin/python' : '.venv\\Scripts\\python.exe'
                            def selected = (env.SELECTED_TESTS ?: '').split(',').findAll { it } as List
                            // pytest with no file paths falls back to pytest.ini testpaths and runs
                            // every test, so never start it without the ticked files.
                            if (!selected || !env.TEST_PATHS?.trim()) {
                                error('No test files selected - refusing to start pytest, it would run every test in tests/')
                            }
                            // Set in 'Select tests': empty unless USE_EC2_NODES is ticked.
                            def nodeLabels = (env.RUN_NODE_LABELS ?: '').split(',').findAll { it }
                            // With USE_EC2_NODES the multi-browser test runs on those agents instead of here.
                            def local = nodeLabels ? selected.findAll { it != MULTI_NODE_TEST } : selected

                            // pytest exit code per run: 0 passed, 1 some tests failed, 2+ run broke.
                            def statuses = [:]
                            def monitor = 'test_active_session_count'
                            def eachInParallel = params.RUN_IN_PARALLEL?.toString() == 'true'
                            // The counter only makes sense alongside the tests it counts: ticked with
                            // others, it runs next to them even without RUN_IN_PARALLEL (the others
                            // then still run one after another, in one pytest, as before).
                            def counterWithOthers = selected.contains(monitor) && selected.size() > 1
                            def inParallel = (eachInParallel && selected.size() > 1) || counterWithOthers || nodeLabels
                            // Parallel branch name -> the test files it runs in one pytest.
                            def runs = [:]
                            if (eachInParallel) {
                                for (name in local) {
                                    runs[name] = [name]
                                }
                            } else {
                                if (local.contains(monitor)) {
                                    runs[monitor] = [monitor]
                                }
                                def rest = local.findAll { it != monitor }
                                if (rest) {
                                    runs[rest.size() == 1 ? rest[0] : 'other-tests'] = rest
                                }
                            }
                            echo "RUN_IN_PARALLEL=${params.RUN_IN_PARALLEL}, ${selected.size()} test file(s) selected -> " +
                                 (!inParallel ? 'running them one after another'
                                  : eachInParallel ? 'starting them all at once'
                                  : runs.containsKey('other-tests') ? "${monitor} alongside the others, which run one after another"
                                  : 'starting them together') +
                                 (nodeLabels ? " (${MULTI_NODE_TEST} on agents ${nodeLabels.join(', ')})" : '')
                            if (inParallel) {
                                // One pytest per file, all at once; each writes its own JUnit
                                // file and Playwright output folder (pytest-playwright empties
                                // it at startup), and Allure results (uniquely named) share one folder.
                                // Logins from all the parallel tests are spaced out
                                // (common/session_retry.py wait_for_login_slot()).
                                env.LOGIN_MIN_INTERVAL_S = (params.PARALLEL_LOGIN_GAP_S ?: '30').trim()
                                echo "Logins across the parallel tests start at least ${env.LOGIN_MIN_INTERVAL_S}s apart"
                                // test_active_session_count keeps counting until every other
                                // test has finished: the last one to finish writes the stop file
                                // it watches (ACTIVE_SESSIONS_STOP_FILE).
                                def watchOthers = selected.contains(monitor)
                                def othersLeft = [count: runs.keySet().findAll { it != monitor }.size() + nodeLabels.size()]
                                if (watchOthers) {
                                    echo "${monitor} counts sessions until the other ${othersLeft.count} test run(s) finish"
                                }
                                def otherFinished = {
                                    if (watchOthers) {
                                        othersLeft.count -= 1
                                        if (othersLeft.count == 0) {
                                            writeFile file: STOP_FILE, text: 'all other tests finished\n'
                                            echo "All other tests finished - telling ${monitor} to stop"
                                        }
                                    }
                                }
                                def branches = [:]
                                // The other tests wait at this gate until the counter is logged in
                                // (READY_FILE), so its first poll comes before their logins and launches.
                                // It opens anyway if the counter ends or takes over 10 min to log in.
                                def counterGate = [open: !watchOthers]
                                def waitForCounter = { String who ->
                                    if (!counterGate.open) {
                                        echo "${who}: waiting for ${monitor} to log in"
                                        waitUntil { counterGate.open }
                                    }
                                }
                                if (watchOthers) {
                                    branches['wait for session counter login'] = {
                                        try {
                                            timeout(time: 10, unit: 'MINUTES') {
                                                waitUntil { counterGate.open || fileExists(READY_FILE) }
                                            }
                                            echo "${monitor} is logged in - starting the other tests"
                                        } catch (e) {
                                            echo "${monitor} not logged in after 10 min - starting the other tests anyway"
                                        } finally {
                                            counterGate.open = true
                                        }
                                    }
                                }
                                for (runName in new ArrayList(runs.keySet())) {
                                    def testName = runName   // the closures must capture this iteration's values
                                    def testPaths = runs[runName].collect { "tests/${it}.py" }.join(' ')
                                    branches[testName] = {
                                        def isMonitor = watchOthers && testName == monitor
                                        if (!isMonitor) {
                                            waitForCounter(testName)
                                        }
                                        echo "${testName}: started"
                                        def branchEnv = isMonitor ? ["ACTIVE_SESSIONS_STOP_FILE=${STOP_FILE}", "ACTIVE_SESSIONS_READY_FILE=${READY_FILE}"] : []
                                        try {
                                            withEnv(branchEnv) {
                                                statuses[testName] = runStatus(
                                                    "${pytestCommand(py)} ${testPaths} ${commonArgs} " +
                                                    "--junitxml=evidence/junit-${testName}.xml --output=test-results/${testName}")
                                            }
                                        } finally {
                                            if (isMonitor) {
                                                counterGate.open = true   // it ended: don't hold the others back
                                            }
                                        }
                                        echo "${testName}: finished (pytest exit code ${statuses[testName]})"
                                        if (testName != monitor) {
                                            otherFinished()
                                        }
                                    }
                                }

                                // The multi-browser test once per agent (USE_EC2_NODES): each sets
                                // up its own workspace, then they all wait until every agent is
                                // ready and start at one agreed Unix time, agent i NODE_START_OFFSET_S
                                // x i later, so their logins don't clash. Results come back with stash.
                                def offsetS = (params.NODE_START_OFFSET_S ?: '30').trim().toInteger()
                                def sync = [ready: 0, startAt: 0L, failed: false]
                                def nodePrefixes = []
                                for (int i = 0; i < nodeLabels.size(); i++) {
                                    def nodeLabel = nodeLabels[i]   // the closure must capture this iteration's values
                                    def index = i
                                    def prefix = "N${i + 1}"
                                    def key = "${MULTI_NODE_TEST} ${prefix}@${nodeLabel}".toString()
                                    nodePrefixes << prefix
                                    branches[key] = {
                                        try {
                                            node(nodeLabel) {
                                                try {
                                                    try {
                                                        checkout scm
                                                        setupPython()
                                                        withCredentials([file(credentialsId: "cape-nft-env-${params.ENVIRONMENT.toLowerCase()}", variable: 'ENV_FILE')]) {
                                                            prepareWorkspace()
                                                        }
                                                    } catch (e) {
                                                        sync.failed = true
                                                        throw e
                                                    }
                                                    sync.ready += 1
                                                    // Set up while the counter logs in; start only once it has.
                                                    waitForCounter(prefix)
                                                    if (sync.ready == nodeLabels.size() && !sync.startAt) {
                                                        // waitUntil re-checks every few seconds (up to 15 s), so leave a margin.
                                                        sync.startAt = (long) (System.currentTimeMillis() / 1000) + 45
                                                    }
                                                    echo "${prefix} (${env.NODE_NAME}): ready, waiting for the other agents"
                                                    waitUntil { sync.startAt > 0 || sync.failed }
                                                    if (sync.failed) {
                                                        error("${prefix}: not started - another agent's setup failed")
                                                    }
                                                    def startAt = sync.startAt + index * offsetS
                                                    echo "${prefix} (${env.NODE_NAME}): sessions named ${prefix}_B<n>_..., starting at Unix time ${startAt}"
                                                    withEnv(["GROUP_SESSIONS_UI_NODE_PREFIX=${prefix}_",
                                                             "GROUP_SESSIONS_UI_START_AT_EPOCH=${startAt}"]) {
                                                        // This agent's own path: it can be Linux while the build's agent is Windows.
                                                        def nodePy = isUnix() ? '.venv/bin/python' : '.venv\\Scripts\\python.exe'
                                                        statuses[key] = runStatus(
                                                            "${pytestCommand(nodePy)} tests/${MULTI_NODE_TEST}.py ${commonArgs} " +
                                                            "--junitxml=evidence/junit-${MULTI_NODE_TEST}-${prefix}.xml " +
                                                            "--output=test-results/${MULTI_NODE_TEST}-${prefix}")
                                                    }
                                                    echo "${prefix} (${env.NODE_NAME}): finished (pytest exit code ${statuses[key]})"
                                                } finally {
                                                    stash name: "results-${prefix}", allowEmpty: true,
                                                          includes: 'evidence/**, test-results/**', excludes: 'evidence/.login_slot*'
                                                    removeEnvFile()
                                                }
                                            }
                                        } finally {
                                            otherFinished()
                                        }
                                    }
                                }
                                branches.failFast = false
                                try {
                                    parallel branches
                                } finally {
                                    // Bring every agent's JUnit, Allure, CSVs and screenshots here for the reports.
                                    for (prefix in nodePrefixes) {
                                        try {
                                            unstash "results-${prefix}"
                                        } catch (e) {
                                            echo "No results from ${prefix}: ${e.message}"
                                        }
                                    }
                                }
                            } else {
                                statuses['pytest'] = runStatus("${pytestCommand(py)} ${env.TEST_PATHS} ${commonArgs} --junitxml=evidence/junit.xml")
                            }

                            // 5 = no tests collected: in parallel, a PYTEST_K filter may
                            // match nothing in some of the files.
                            if (kArg && statuses.size() > 1) {
                                statuses.findAll { it.value == 5 }.each { echo "${it.key}: no tests match PYTEST_K" }
                                statuses = statuses.findAll { it.value != 5 }
                            }
                            def broken = statuses.findAll { it.value > 1 }
                            if (broken) {
                                error('pytest run broke: ' + broken.collect { "${it.key} exited with code ${it.value}" }.join(', '))
                            } else if (statuses.any { it.value == 1 }) {
                                unstable('Some tests failed: ' + statuses.findAll { it.value == 1 }.keySet().join(', '))
                            }
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'evidence/junit*.xml', allowEmptyResults: true
            // Always: one folder per test file per run, evidence/runs/<time>_<test file>/ (CSVs,
            // charts, JSON reports, failure/step screenshots, stuck-browser and
            // session-drop captures - see config/config.py evidence_dir()), and the
            // failure screenshots of tests using the page fixture (test-results/).
            archiveArtifacts artifacts: 'evidence/runs/**, evidence/**/*.csv, evidence/**/*.json, test-results/**',
                             excludes: 'evidence/**/output_*, evidence/allure-results/**', allowEmptyArchive: true
            script {
                if (params.CAPTURE_TEST_DATA?.toString() == 'true') {
                    archiveArtifacts artifacts: 'evidence/**/output_*, testdata/session_ids_U*.csv', allowEmptyArchive: true
                }
            }
            script {
                try {
                    allure results: [[path: 'evidence/allure-results']]
                } catch (NoSuchMethodError ignored) {
                    echo 'Allure plugin not installed - skipping Allure report (raw results are in evidence/allure-results).'
                } catch (Exception e) {
                    // e.g. plugin installed but no Allure commandline tool configured
                    echo "Allure report skipped: ${e.message}"
                }
            }
            script {
                if (params.DOWNLOAD_ALLURE_REPORT?.toString() != 'false') {
                    downloadableAllureReport()
                }
            }
            script { sendResultEmail() }
            script { removeEnvFile() }
        }
    }
}