pipeline {
    agent any

    environment {
        IMAGE_NAME = "lulc-classifier"
        CONTAINER_NAME = "lulc-app"
    }

    stages {

        stage('Clone Repo') {
            steps {
                git branch: 'main',
                url: 'https://github.com/HazemNasr24/ECE435_Project_2026.git'
            }
        }

        stage('Create Persistent Storage') {
            steps {
                sh '''
                # إضافة فولدر chunks
                mkdir -p /opt/lulc/uploads
                mkdir -p /opt/lulc/chunks
                chmod -R 777 /opt/lulc
                '''
            }
        }

        stage('Build Docker Image') {
            steps {
                sh '''
                docker build -t $IMAGE_NAME .
                '''
            }
        }

        stage('Stop Old Container') {
            steps {
                sh '''
                docker stop $CONTAINER_NAME || true
                docker rm $CONTAINER_NAME || true
                '''
            }
        }

        stage('Run New Container') {
            steps {
                sh '''
                docker run -d \
                    --name $CONTAINER_NAME \
                    --restart always \
                    -p 5000:5000 \
                    -v /opt/lulc/uploads:/app/uploads \
                    -v /opt/lulc/chunks:/app/chunks \
                    $IMAGE_NAME
                '''
            }
        }

        stage('Cleanup Docker') {
            steps {
                sh '''
                docker image prune -f
                '''
            }
        }
    }

    post {

        success {
            echo 'Deployment Successful 🚀'
        }

        failure {
            echo 'Deployment Failed ❌'
        }
    }
}