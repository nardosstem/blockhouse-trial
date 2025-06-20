import pandas as pd 
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
import json, time
import os

def parse_file(path, start_time, end_time):
    """
    Parses the csv file and filters rows with ts_event between start_time and end_time.
    Returns a list of dicts with publisher_id, ask_px_00, ask_sz_00 and ts_event.
    """
    df = pd.read_csv(path, parse_dates=['ts_event'])
    rows = df[(df['ts_event'] >= start_time) & (df['ts_event'] <= end_time)]
    results = rows[['publisher_id', 'ask_px_00', 'ask_sz_00', 'ts_event']].to_dict(orient='records')
    return [
        {
            "publisher_id": r["publisher_id"],
            "ask_px_00":  r["ask_px_00"],
            "ask_sz_00":  r["ask_sz_00"],
            "ts_event":   r["ts_event"].isoformat()     
        }
        for r in results
    ]

def kafka_messages(records, topic, servers):
    """
    Connects to kafka, iterates through records, and sends each record as a message to the
    specified topic.
    """

    admin = KafkaAdminClient(
        bootstrap_servers=[servers],
        client_id="auto-topic-creator"
    )
    try:
        admin.create_topics([
            NewTopic(name=topic, num_partitions=1, replication_factor=1)
        ])
        print(f"[INIT] Created topic `{topic}`")
    except TopicAlreadyExistsError:
        # perfectly fine if someone else already made it
        pass
    finally:
        admin.close()

    producer = KafkaProducer(
        bootstrap_servers=[servers],
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
        api_version=(0, 10, 1)
    )
    
    previous_ts = None
    for record in records:
        current_ts = pd.to_datetime(record['ts_event'])
        if previous_ts is not None:
            delta = (current_ts - previous_ts).total_seconds()
            if delta < 0.1:
                delta = 0.1 # Set miniumum sleep time to avoid flooding the topic
            time.sleep(delta)  # Sleep for the time difference between messages
        
        previous_ts = current_ts
        sent = producer.send(topic, value=record)
        # Try and wait for the message to be sent
        try:
            sent.get(timeout=10)
            print(f"Sent: {record}")
        except Exception as e:
            print(f"Failed to send message: {e}")
    producer.flush()
    producer.close()

def main():
    path = 'data/l1_day.csv'
    start_ts = '2024-08-01T13:36:32'
    end_ts = '2024-08-01T13:45:14'
    topic = 'mock_l1_stream'
    host = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')

    records = parse_file(path, start_ts, end_ts)
    if not records:
        print("[DEBUG] No records in that time window—check your CSV mount or timestamps!")
        return

    kafka_messages(records, topic, host)

if __name__ == "__main__":
    main()