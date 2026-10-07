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
    'test_us_167'                                         : 'US 167 - high-throughput script, timed',
    'test_us_168'                                         : 'US 168 - high-throughput script, timed',
    'test_launch_sessions_api_user1'                      : 'Create / relaunch sessions through the API only',
    'test_create_session_through_api_and_run_script'      : 'API-created sessions driven in the browser',
    'test_create_session_through_api_and_run_script_group': 'API-created sessions, mixed workloads',
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
    }

    stages {
        stage('Select tests') {
            steps {
                script {
                    def selected = params.RUN_ALL ? TEST_NAMES : TEST_NAMES.findAll { params[it] }
                    if (!selected) {
                        error('No tests selected. Tick at least one test checkbox (or RUN_ALL) and build again.')
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
                            python3 -m venv .venv
                            . .venv/bin/activate
                            pip install --upgrade pip
                            pip install -r requirements.txt
                            playwright install chromium
                        '''
                    } else {
                        bat '''
                            python -m venv .venv
                            call .venv\\Scripts\\activate.bat
                            python -m pip install --upgrade pip
                            pip install -r requirements.txt
                            playwright install chromium
                        '''
                    }
                }
            }
        }

        stage('Run tests') {
            steps {
                // The tests load .env from the repo root (common/config.py).
                // Dev -> 'cape-nft-env-dev', QA -> 'cape-nft-env-qa'.
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
                        def activate = isUnix() ? '. .venv/bin/activate' : 'call .venv\\Scripts\\activate.bat'
                        def status = runStatus("${activate} && python -m pytest ${args}")

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
