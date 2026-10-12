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
    // First, so its checkbox sits right below RUN_IN_PARALLEL.
    'test_active_session_count'                           :
        'Count Active + Executing sessions every 10 s. It logs in first, the other ticked tests ' +
        'start once it has (it runs alongside them), and it stops when they all finish or after ACTIVE_SESSIONS_MONITOR_S (20 min)',
    'test_create_and_launch_single_session'             : 'One new session reaches its IDE',
    'test_create_and_launch_multiple_sessions'            : 'Several sessions',
    'test_create_and_launch_multiple_sessions_user1'      : 'Several sessions, user 1, R scripts in tabs',
    'test_create_and_launch_multiple_sessions_user2'      : 'Several sessions, user 2, R scripts in tabs',
    'test_two_user_login'                                 : 'Two users signed in at once',
    'test_rstudio_sessions'                               : 'Sessions stay alive while held idle',
    'test_rstudio_session_perf'                           : 'Launch time (single / sequential / concurrent), projects, text files',
    'test_rstudio_run_r_script_perf'                      : 'R script time in new sessions, one or many users',
    'test_rstudio_source_script_perf'                     : 'R script time in existing sessions',
    'test_create_sessions_run_r_close_browser'            : 'Scripts and Workbench jobs keep running after the tab closes',
    'test_run_high_throughput_job'                        : 'High-throughput script, timed',
    'test_167'                                            : 'US 167 - high-throughput script, timed',
    'test_168'                                            : 'US 168 - high-throughput script, timed',
    'test_launch_sessions_api_user1'                      : 'Create / relaunch sessions through the API only',
    'test_create_session_through_api_and_run_script'      : 'API-created sessions driven in the browser',
    'test_create_session_through_api_and_run_script_group': 'API-created sessions, mixed workloads',
    'test_create_session_through_ui_and_run_script_group' : 'UI-created sessions, mixed workloads',
    'test_create_session_through_ui_and_run_script_group_multi_browser':
        'UI-created sessions, mixed workloads, in UI_BROWSERS browsers launching in parallel (B1_, B2_, ... name prefixes)',
    'test_launch_existing_session_and_run_r_scripts'      : 'Reuse existing API sessions',
    'test_concurrent_sessions'                            :
        'CONCURRENT_SESSIONS UI-created sessions (CONCURRENT_SESSION_1, _2, ...) over CONCURRENT_SESSIONS_BROWSERS browsers, ' +
        'R script sourced in all at once and timed',
]
TEST_NAMES = new ArrayList(TEST_FILES.keySet())
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
def jobParams = [
    choice(name: 'ENVIRONMENT', choices: ['Dev', 'QA'], description: 'Target environment - picks which .env credential the tests use'),
    booleanParam(name: 'RUN_ALL', defaultValue: false, description: 'Run every test file (ignores the checkboxes below)'),
    booleanParam(name: 'HEADED', defaultValue: false,
        description: 'Run the browsers headed (with windows) instead of headless. On a Linux agent they open on a ' +
                     'virtual 1920x1080 screen (Xvfb, must be installed there); on a Windows agent on its desktop'),
    booleanParam(name: 'RUN_IN_PARALLEL', defaultValue: false,
        description: 'Run the selected test files at the same time, each in its own pytest (one parallel branch per file). ' +
                     'Only for tests that keep to their own sessions, e.g. test_167 + test_168; tests that quit every ' +
                     'automation session or reuse the same session names will interfere with each other.'),
]
for (name in TEST_NAMES) {
    jobParams << booleanParam(name: name, defaultValue: false, description: TEST_FILES[name])
}
jobParams << string(name: 'SESSION_COUNT', defaultValue: '10',
    description: 'RSTUDIO_SESSION_COUNT for the multi-session tests (e.g. test_168, test_create_sessions_run_r_close_browser): ' +
                 'sessions each test launches. Overrides the value in .env. With USE_EC2_NODES this is per agent')
jobParams << string(name: 'UI_BROWSERS', defaultValue: '2',
    description: 'Multi-browser test: number of browsers. Each opens one tab per session name in the group CSVs ' +
                 '(5 today), so 2 browsers = 10 sessions')
jobParams << string(name: 'CONCURRENT_SESSIONS', defaultValue: '10',
    description: 'test_concurrent_sessions: number of sessions launched and running the R script at the same time')
jobParams << string(name: 'CONCURRENT_SESSIONS_BROWSERS', defaultValue: '2',
    description: 'test_concurrent_sessions: number of browsers the sessions are split over, one tab per session. ' +
                 'Keep it to 5-6 tabs per browser (e.g. 10 sessions -> 2, 30 -> 5). Blank = one browser per 5 sessions')
jobParams << string(name: 'UI_BROWSER_STAGGER_S', defaultValue: '30',
    description: 'Multi-browser test and test_concurrent_sessions: seconds between starting one browser and the next, so logins do not clash')
jobParams << booleanParam(name: 'USE_EC2_NODES', defaultValue: false,
    description: 'Run the multi-browser test on the agents in NODE_LABELS (e.g. EC2) instead of this build\'s agent. ' +
                 'Unticked = run everything here and ignore NODE_LABELS')
jobParams << string(name: 'NODE_LABELS', defaultValue: 'cape-nft-load-1,cape-nft-load-2',
    description: 'With USE_EC2_NODES: agent labels separated by commas. The multi-browser test runs once per label at the ' +
                 'same time, as the same user, with UI_BROWSERS browsers each and N1_, N2_, ... in front of the session ' +
                 'names (so 2 labels x 4 browsers x 5 names = 40 sessions). The other ticked tests run on this build\'s ' +
                 'agent alongside')
jobParams << string(name: 'NODE_START_OFFSET_S', defaultValue: '30',
    description: 'With USE_EC2_NODES: seconds between one agent starting its browsers and the next, so logins from ' +
                 'different agents do not land together. Keep UI_BROWSER_STAGGER_S >= agents x this (e.g. 2 agents: 30 and 60)')
jobParams << string(name: 'PARALLEL_LOGIN_GAP_S', defaultValue: '30',
    description: 'With RUN_IN_PARALLEL: minimum seconds between any two logins across all the parallel tests ' +
                 '(Workbench fails sign-ins that happen at the same time)')
jobParams << string(name: 'GROUP_TEST_DURATION_S', defaultValue: '180',
    description: 'Group tests (API, UI and multi-browser): how long the workloads run, in seconds')
jobParams << string(name: 'HIGH_THROUGHPUT_DURATION_S', defaultValue: '',
    description: 'test_167 / test_168: seconds each high-throughput script run may take before it counts as failed. ' +
                 'Blank = HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS from .env (180 if unset)')
jobParams << string(name: 'CLOSE_BROWSER_WAIT_S', defaultValue: '',
    description: 'test_create_sessions_run_r_close_browser: seconds the sessions are left running with their tabs closed ' +
                 'before they are quit. Blank = RSTUDIO_WORKBENCH_JOB_TEST_DURATION_SECONDS from .env (300 if unset)')
jobParams << string(name: 'ACTIVE_SESSIONS_MONITOR_S', defaultValue: '1200',
    description: 'test_active_session_count: the longest it keeps counting, in seconds (1200 = 20 min). With other tests ' +
                 'ticked it stops sooner, once they have all finished; ticked on its own it counts for this long')
jobParams << string(name: 'ACTIVE_SESSIONS_USERS', defaultValue: '1',
    description: 'test_active_session_count: users whose sessions are counted and added up, e.g. "1,2"')
jobParams << string(name: 'PYTEST_K', defaultValue: '', description: 'Optional pytest -k filter applied within the selected files (e.g. "concurrent")')
jobParams << string(name: 'EXTRA_PYTEST_ARGS', defaultValue: '', description: 'Optional extra pytest arguments (e.g. "-x" or "--maxfail=2")')

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
// repo root, common/config.py) and drop the last build's results, since the
// workspace is reused between builds. Call inside withCredentials(ENV_FILE).
// SESSION_COUNT and HIGH_THROUGHPUT_DURATION_S are appended to .env rather than
// passed with withEnv, because .env is loaded with override=True and
// python-dotenv keeps the last value.
def prepareWorkspace() {
    def overrides = ["RSTUDIO_SESSION_COUNT=${(params.SESSION_COUNT ?: '10').trim()}"]
    def htpDuration = (params.HIGH_THROUGHPUT_DURATION_S ?: '').trim()
    if (htpDuration) {
        overrides << "HIGH_THROUGHPUT_JOB_TEST_DURATION_SECONDS=${htpDuration}"
    }
    if (isUnix()) {
        // Jenkins keeps secret files read-only and cp keeps that mode: make the
        // copy writable, since the overrides below are appended to it.
        sh 'rm -f .env && cp "$ENV_FILE" .env && chmod 600 .env'
        sh "printf '\\n%s\\n' ${overrides.collect { "'${it}'" }.join(' ')} >> .env"
        sh "rm -rf evidence/junit*.xml evidence/allure-results evidence/.login_slot.lock ${STOP_FILE} ${READY_FILE}"
    } else {
        bat '(if exist .env del /F /Q .env) && copy /Y "%ENV_FILE%" .env && attrib -R .env'
        bat "(echo.${overrides.collect { "& echo ${it}" }.join('')})>> .env"
        bat '''
            if exist evidence\\junit*.xml del /F /Q evidence\\junit*.xml
            if exist evidence\\.login_slot.lock del /F /Q evidence\\.login_slot.lock
            if exist evidence\\.active_sessions_stop del /F /Q evidence\\.active_sessions_stop
            if exist evidence\\.active_sessions_ready del /F /Q evidence\\.active_sessions_ready
            if exist evidence\\allure-results rmdir /S /Q evidence\\allure-results
            exit /b 0
        '''
    }
}

// The command that starts pytest with the agent's Python (pyPath). HEADED on a
// Linux agent runs it under Xvfb, a virtual screen, since a server has no
// display; BROWSER_WINDOW_SIZE sizes the windows there (common/config.py).
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
                    def selected = params.RUN_ALL ? TEST_NAMES : TEST_NAMES.findAll { params[it] }
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
                    def atLeastOne = ['UI_BROWSERS', 'SESSION_COUNT', 'CONCURRENT_SESSIONS']
                    ['UI_BROWSERS', 'SESSION_COUNT', 'CONCURRENT_SESSIONS', 'UI_BROWSER_STAGGER_S', 'PARALLEL_LOGIN_GAP_S', 'GROUP_TEST_DURATION_S',
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
                        (selected.size() == TEST_NAMES.size() ? 'all tests' : selected.join(', ')) +
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
                // The tests load .env from the repo root (common/config.py).
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
                ]) {
                    withCredentials([file(credentialsId: "cape-nft-env-${params.ENVIRONMENT.toLowerCase()}", variable: 'ENV_FILE')]) {
                        script {
                            prepareWorkspace()

                            def kArg = params.PYTEST_K?.trim() ? "-k \"${params.PYTEST_K.trim()}\"" : ''
                            def commonArgs = "-v ${kArg} ${params.HEADED?.toString() == 'true' ? '--headed' : ''} ${params.EXTRA_PYTEST_ARGS ?: ''} --alluredir=evidence/allure-results"
                            def py = isUnix() ? '.venv/bin/python' : '.venv\\Scripts\\python.exe'
                            def selected = env.SELECTED_TESTS.split(',') as List
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
            archiveArtifacts artifacts: 'evidence/**/*.csv, evidence/**/*.json, testdata/session_ids_U*.csv, test-results/**',
                             allowEmptyArchive: true
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
            script { removeEnvFile() }
        }
    }
}