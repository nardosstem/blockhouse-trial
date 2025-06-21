# blockhouse-trial

**Video Walkthrough:** <https://youtu.be/NRmzJ5LDuSw>

A self-contained pipeline that:  
1. **Streams** Level-1 snapshots from `l1_day.csv` into Kafka  
2. **Backtests** a Cont-Kukanov Smart-Order-Routing allocator  
3. **Benchmarks** vs Best-Ask, TWAP & VWAP  
4. **Deploys** on AWS EC2 with Docker-Compose  

---

## Quickstart (Local)

1. **Clone** the repo  
   ```bash
   git clone https://github.com/nardosstem/blockhouse-trial.git
   cd blockhouse-trial
    ```

2. **Install Dependencies**
    ```bash
    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    ```

3. **Launch Docker**
    ```bash
    docker compose up
    ```

4. **View results in console**


---

## More on Dockerized Setup
All components run as Docker services for reproducibility and easy local testing.

1. **Build the unified image**  
   (contains both `kafka_producer.py` and `backtest.py`, plus shared dependencies)
   ```bash
   docker build -t blockhouse-pipeline .
    ```

Here’s the relevant excerpt from docker-compose.yml:
```yaml
    version: '3.8'
    services:
    zookeeper:
        image: confluentinc/cp-zookeeper:7.5.0
        environment:
        ZOOKEEPER_TICK_TIME: 3000
        ZOOKEEPER_INIT_LIMIT: 10
        ZOOKEEPER_SYNC_LIMIT: 5
        healthcheck:
        test: ["CMD", "bash", "-c", "echo > /dev/tcp/localhost/2181"]
        interval: 10s
        retries: 5

    kafka:
        image: confluentinc/cp-kafka:7.5.0
        environment:
        KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
        KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
        KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT
        KAFKA_HEAP_OPTS: "-Xms128M -Xmx256M -XX:+UseG1GC -XX:MaxGCPauseMillis=100"
        depends_on:
        zookeeper:
            condition: service_healthy
        healthcheck:
        test: ["CMD", "bash", "-c", "echo > /dev/tcp/localhost/9092"]
        interval: 10s
        retries: 6

    producer:
        image: blockhouse-pipeline
        command: python kafka_producer.py
        depends_on:
        kafka:
            condition: service_healthy

    backtester:
        image: blockhouse-pipeline
        command: python backtest.py
        depends_on:
        producer:
            condition: service_completed_successfully
```
2. **Run the containers***
    ```bash
    docker compose up
    ```

Zookeeper and Kafka start first with healthchecks.
Producer streams your l1_day.csv snapshots once Kafka is ready.
Backtester launches when the producer completes, then prints the JSON result to stdout.

---

## AWS EC2 Deployment

I used CloudFormation (pipeline.yml) to provision a single Amazon Linux 2 EC2 that installs Docker, pulls the repository, and launches the Docker Compose stack on boot.

Deployment Instructions:

1. Use AWS CLI to generate a keyPair and deploy
    ```bash
    aws ec2 create-key-pair \
    --key-name blockhouseTrial \
    --query 'KeyMaterial' \
    --output text > ~/.ssh/blockhouseTrial.pem 
    ```
    ```bash
    chmod 400 ~/.ssh/blockhouseTrial.pem
    ```

    ```bash
    aws cloudformation deploy \
    --stack-name blockhouse-pipeline \
    --template-file pipeline.yml \
    --parameter-overrides KeyName=blockhouseTrial 
    ```



2.	Fill in parameters
	•	Create or note your EC2 KeyPair name.
	•	Optionally change InstanceType to t2.micro (trial) or t3.small (recommended).

3.	Deploy with AWS CLI
    ```bash
    aws cloudformation deploy \
    --stack-name blockhouse-pipeline \
    --template-file pipeline.yml \
    --parameter-overrides \
        KeyName=blockhouseTrial \
        InstanceType=t3.small \
        GitHubRepo=https://github.com/nardosstem/blockhouse-trial.git \
    --capabilities CAPABILITY_NAMED_IAM
    ```

4.	SSH & verify
    ```bash
    INSTANCE_IP=$(aws cloudformation describe-stacks \
    --stack-name blockhouse-pipeline \
    --query "Stacks[0].Outputs[?OutputKey=='InstancePublicIP'].OutputValue" \
    --output text)

    ssh -i ~/blockhouseTrial.pem ec2-user@$INSTANCE_IP
    docker ps
    docker compose logs --tail=20
    ```

The EC2 instance will auto-install Docker, pull the repo, and run the Kafka → producer → backtester pipeline under Docker Compose.

---
## Files and Structure 
.
├── Dockerfile
├── docker-compose.yml
├── kafka_producer.py
├── backtest.py
├── requirements.txt
├── pipeline.yml
└── README.md

