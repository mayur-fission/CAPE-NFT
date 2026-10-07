pipeline {
    agent any

    parameters {
        choice(name: 'ENV', choices: ['dev', 'qa', 'staging'], description: 'Target environment')
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }
        stage('Run Tests') {
            steps {
                sh "python run_tests.py --env ${params.ENV}"
            }
        }
    }
}