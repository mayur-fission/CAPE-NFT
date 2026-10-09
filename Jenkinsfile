// CAPE NFT - Posit Workbench performance tests.
//
// "Build with Parameters" shows one checkbox per test file; tick any number
// and they run together in a single pytest run. RUN_ALL ignores the boxes.
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

// Test file (without .py) -> checkbox description, in the order shown in Jenkins.
// No `def`: a script-level `def` is not reliably visible inside pipeline blocks.
TEST_FILES = [
    'test_create_and_launch_single_session'               : 'One new session reaches its IDE',
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
]
TEST_NAMES = new ArrayList(TEST_FILES.keySet())

// Job parameters and settings, in display order. Set with properties() rather
// than a declarative parameters {} block because properties() REPLACES the
// job's whole parameter list on every build: a stale parameter (e.g. the old
// ENV dropdown) or one added by hand in the Jenkins UI is removed, and the
// order always matches this list.
def jobParams = [
    choice(name: 'ENVIRONMENT', choices: ['Dev', 'QA'], description: 'Target environment - picks which .env credential the tests use'),
    booleanParam(name: 'RUN_ALL', defaultValue: false, description: 'Run every test file (ignores the checkboxes below)'),
]
for (name in TEST_NAMES) {
    jobParams << booleanParam(name: name, defaultValue: false, description: TEST_FILES[name])
}
jobParams << string(name: 'UI_BROWSERS', defaultValue: '2',
    description: 'Multi-browser test: number of browsers. Each opens one tab per session name in the group CSVs ' +
                 '(5 today), so 2 browsers = 10 sessions')
jobParams << string(name: 'UI_BROWSER_STAGGER_S', defaultValue: '30',
    description: 'Multi-browser test: seconds between starting one browser and the next, so logins do not clash')
jobParams << string(name: 'GROUP_TEST_DURATION_S', defaultValue: '180',
    description: 'Group tests (API, UI and multi-browser): how long the workloads run, in seconds')
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
                    if (!selected) {
                        error('No tests selected. Tick at least one test checkbox (or RUN_ALL) and build again.')
                    }
                    ['UI_BROWSERS', 'UI_BROWSER_STAGGER_S', 'GROUP_TEST_DURATION_S'].each { name ->
                        def value = params[name]?.trim()
                        if (!(value ==~ /\d+/) || (name == 'UI_BROWSERS' && value.toInteger() < 1)) {
                            error("${name} must be a whole number${name == 'UI_BROWSERS' ? ' of at least 1' : ''}, got '${params[name]}'")
                        }
                    }
                    env.TEST_PATHS = selected.collect { "tests/${it}.py" }.join(' ')
                    currentBuild.description = "${params.ENVIRONMENT}: " +
                        (selected.size() == TEST_NAMES.size() ? 'all tests' : selected.join(', '))
                    echo "Running ${selected.size()} test file(s) on ${params.ENVIRONMENT}:\n  " + selected.join('\n  ')
                }
            }
        }

        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Set up Python') {
            steps {
                script {
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
                ]) {
                    withCredentials([file(credentialsId: "cape-nft-env-${params.ENVIRONMENT.toLowerCase()}", variable: 'ENV_FILE')]) {
                        script {
                            if (isUnix()) {
                                sh 'cp "$ENV_FILE" .env'
                            } else {
                                bat 'copy /Y "%ENV_FILE%" .env'
                            }

                            def kArg = params.PYTEST_K?.trim() ? "-k \"${params.PYTEST_K.trim()}\"" : ''
                            def args = "${env.TEST_PATHS} -v ${kArg} ${params.EXTRA_PYTEST_ARGS ?: ''} " +
                                       '--junitxml=evidence/junit.xml --alluredir=evidence/allure-results'
                            def py = isUnix() ? '.venv/bin/python' : '.venv\\Scripts\\python.exe'
                            def status = runStatus("${py} -m pytest ${args}")

                            // pytest exit codes: 0 passed, 1 some tests failed, 2+ run broke.
                            if (status == 1) {
                                unstable('Some tests failed')
                            } else if (status > 1) {
                                error("pytest exited with code ${status}")
                            }
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'evidence/junit.xml', allowEmptyResults: true
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
            // .env holds credentials; never leave it in the workspace.
            script { run(isUnix() ? 'rm -f .env' : 'if exist .env del /F /Q .env') }
        }
    }
}