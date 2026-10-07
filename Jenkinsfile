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

// No `def`: a script-level `def` is not reliably visible inside pipeline blocks.
TEST_FILES = [
    'test_create_and_launch_single_session',
    'test_create_and_launch_multiple_sessions',
    'test_create_and_launch_multiple_sessions_user1',
    'test_create_and_launch_multiple_sessions_user2',
    'test_two_user_login',
    'test_rstudio_sessions',
    'test_rstudio_session_perf',
    'test_rstudio_run_r_script_perf',
    'test_rstudio_source_script_perf',
    'test_create_sessions_run_r_close_browser',
    'test_run_high_throughput_job',
    'test_us_167',
    'test_us_168',
    'test_launch_sessions_api_user1',
    'test_create_session_through_api_and_run_script',
    'test_create_session_through_api_and_run_script_group',
    'test_launch_existing_session_and_run_r_scripts',
]

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
        buildDiscarder(logRotator(numToKeepStr: '30'))
        disableConcurrentBuilds()   // tests create real sessions on one server
    }

    parameters {
        choice(name: 'ENVIRONMENT', choices: ['Dev', 'QA'], description: 'Target environment - picks which .env credential the tests use')
        booleanParam(name: 'RUN_ALL', defaultValue: false, description: 'Run every test file (ignores the checkboxes below)')
        booleanParam(name: 'test_create_and_launch_single_session', defaultValue: false, description: 'One new session reaches its IDE')
        booleanParam(name: 'test_create_and_launch_multiple_sessions', defaultValue: false, description: 'Several sessions')
        booleanParam(name: 'test_create_and_launch_multiple_sessions_user1', defaultValue: false, description: 'Several sessions, user 1, R scripts in tabs')
        booleanParam(name: 'test_create_and_launch_multiple_sessions_user2', defaultValue: false, description: 'Several sessions, user 2, R scripts in tabs')
        booleanParam(name: 'test_two_user_login', defaultValue: false, description: 'Two users signed in at once')
        booleanParam(name: 'test_rstudio_sessions', defaultValue: false, description: 'Sessions stay alive while held idle')
        booleanParam(name: 'test_rstudio_session_perf', defaultValue: false, description: 'Launch time (single / sequential / concurrent), projects, text files')
        booleanParam(name: 'test_rstudio_run_r_script_perf', defaultValue: false, description: 'R script time in new sessions, one or many users')
        booleanParam(name: 'test_rstudio_source_script_perf', defaultValue: false, description: 'R script time in existing sessions')
        booleanParam(name: 'test_create_sessions_run_r_close_browser', defaultValue: false, description: 'Scripts and Workbench jobs keep running after the tab closes')
        booleanParam(name: 'test_run_high_throughput_job', defaultValue: false, description: 'High-throughput script, timed')
        booleanParam(name: 'test_us_167', defaultValue: false, description: 'US 167 - high-throughput script, timed')
        booleanParam(name: 'test_us_168', defaultValue: false, description: 'US 168 - high-throughput script, timed')
        booleanParam(name: 'test_launch_sessions_api_user1', defaultValue: false, description: 'Create / relaunch sessions through the API only')
        booleanParam(name: 'test_create_session_through_api_and_run_script', defaultValue: false, description: 'API-created sessions driven in the browser')
        booleanParam(name: 'test_create_session_through_api_and_run_script_group', defaultValue: false, description: 'API-created sessions, mixed workloads')
        booleanParam(name: 'test_launch_existing_session_and_run_r_scripts', defaultValue: false, description: 'Reuse existing API sessions')

        string(name: 'PYTEST_K', defaultValue: '', description: 'Optional pytest -k filter applied within the selected files (e.g. "concurrent")')
        string(name: 'EXTRA_PYTEST_ARGS', defaultValue: '', description: 'Optional extra pytest arguments (e.g. "-x" or "--maxfail=2")')
    }

    environment {
        PYTHONUNBUFFERED = '1'
    }

    stages {
        stage('Select tests') {
            steps {
                script {
                    def selected = params.RUN_ALL ? TEST_FILES : TEST_FILES.findAll { params[it] }
                    if (!selected) {
                        error('No tests selected. Tick at least one test checkbox (or RUN_ALL) and build again.')
                    }
                    env.TEST_PATHS = selected.collect { "tests/${it}.py" }.join(' ')
                    currentBuild.description = "${params.ENVIRONMENT}: " +
                        (selected.size() == TEST_FILES.size() ? 'all tests' : selected.join(', '))
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
