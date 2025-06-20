import itertools
from kafka import KafkaConsumer
import json
import pandas as pd
from types import SimpleNamespace
from itertools import groupby
from datetime import timedelta
import os

def consume_snapshots(topic, servers):
    """
    Connects to Kafka and consumes messages from the specified topic.
    Returns a list of snapshots.
    """
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=[servers],
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        group_id='backtest-group',
        value_deserializer=lambda x: json.loads(x.decode('utf-8'))
    )

    snapshots = []
    for i, message in enumerate(consumer):
        record = message.value
        record['ts_event'] = pd.to_datetime(record['ts_event'])
        snapshots.append({
            'venue_id': record['publisher_id'],
            'ask_px': record['ask_px_00'],
            'ask_sz': record['ask_sz_00'],
            'ts_event': record['ts_event']
        })

    consumer.close()
    return snapshots

def allocate(order_size, venues, λ_over, λ_under, θ_queue):
    """
    Allocate function as described in allocator_psuedocode.txt.
    Args:
        order_size (int): target shares to buy (e.g. 5_000)
        venues (list): list of objects, one per venue, each with: .ask, .ask_size, .fee , .rebate
        λ_over (float): cost penalty per extra share bought
        λ_under (float): cost penalty per unfilled share
        θ_queue (float): queue-risk penalty (linear in total mis-execution)
    Output:
        best_split(list[int]): shares sent to each venue (len == N)
        best_cost(float): total expected cost of that  split
    """
    step = 100
    splits = [[]]

    for v in range(0, len(venues)):
        new_splits = []
        for alloc in splits:
           used = sum(alloc)
           max_v = min(order_size - used, venues[v].ask_size)
           for q in range(0, max_v + step, step):
               new_splits.append(alloc + [q])
        splits = new_splits

    best_cost = float('inf')
    best_split = []
    for alloc in splits:
        if sum(alloc) != order_size:
            continue
        cost = compute_cost(alloc, venues, order_size, λ_over, λ_under, θ_queue)
        if cost < best_cost:
            best_cost = cost
            best_split = alloc
    return best_split, best_cost

def compute_cost(split, venues, order_size, λo, λu, θ):
    """
    Computing cost as described in the allocator_psuedocode.txt.
    Args:
        alloc (list[int]): shares sent to each venue
        venues (list): list of objects, one per venue, each with: .ask, .ask_size, .fee , .rebate
        order_size (int): target shares to buy (e.g. 5_000)
        λo (float): cost penalty per extra share bought
        λu (float): cost penalty per unfilled share
        θ (float): queue-risk penalty (linear in total mis-execution)
    Output:
        cost(float): total expected cost of that split
    """
    executed = 0
    cash_spent = 0
    for i in range(len(venues)):
        exe = min(split[i], venues[i].ask_size)
        executed += exe
        cash_spent += exe * (venues[i].ask + venues[i].fee)
        maker_rebate = max(split[i]-exe, 0) * venues[i].rebate
        cash_spent -= maker_rebate
    
    underfill = max(order_size - executed, 0)
    overfill = max(executed - order_size, 0)
    risk_pen = θ * abs(executed - order_size)
    cost_pen = λo * overfill + λu * underfill
    return cash_spent + cost_pen + risk_pen

def execute_order(split, venues):
    """
    Execute the split across the venues.
    Returns:
        executed (int): total shares executed
        cash_spent (float): total (net) cash spent
    """
    executed = 0
    cash_spent = 0.0
    for i, venue in enumerate(venues):
        exe = min(split[i], venue.ask_size)
        executed += exe
        cash_spent += exe * (venue.ask + venue.fee)
        maker_rebate = max(split[i] - exe, 0) 
        cash_spent -= maker_rebate
    return executed, cash_spent

def backtest(order_size, λ_over, λ_under, θ_queue, topic, bootstrap_servers):
    """
    Tracks unfilled shares across snapshots and complete the full order
    """
    snapshots = consume_snapshots(topic, bootstrap_servers)
    snapshots.sort(key=lambda x: x['ts_event'])
    grouped_snapshots = [
        list(group) for _, group in groupby(snapshots, key=lambda x: x['ts_event'])
    ]

    unfilled = order_size
    total_spent = 0.0
    total_executed = 0
    for snapshot in grouped_snapshots:
        venues = [
            SimpleNamespace(
                ask_px=s['ask_px'],
                ask_sz=s['ask_sz'],
                fee=s.get('fee', 0.0),
                rebate=s.get('rebate', 0.0)
            )
            for s in snapshot
        ]
        split, _ = allocate(order_size, venues, λ_over, λ_under, θ_queue)
        executed, cash_spent = execute_order(split, venues)
        total_executed += executed
        total_spent += cash_spent
        unfilled -= executed

        if unfilled <= 0:
            break
    avg_fill_price = total_spent / total_executed if total_executed > 0 else 0.0
    return {
        'total_executed': total_executed,
        'total_cash': total_spent,
        'avg_fill_px': avg_fill_price
    }

def search(λ_over_choices, λ_under_choices, θ_queue_choices, order_size, topic, bootstrap_servers):
    """
    Searches for the best parameters by backtesting across a grid of choices.
    """
    best = {
        'params': None,
        'total_cash': float('inf'),
        'avg_fill_px': None
    }
    for lo, lu, tq in itertools.product(
            λ_over_choices,
            λ_under_choices,
            θ_queue_choices
        ):
        result = backtest(order_size, lo, lu, tq, topic, bootstrap_servers)
    
        if result['total_cash'] < best['total_cash']:
            best.update({
                'params': (lo, lu, tq),
                'total_cash': result['total_cash'],
                'avg_fill_px': result['avg_fill_px']
            })

    return best

def backtest_best_ask(order_size, topic, bootstrap_servers):
    """
    Backtest the best ask strategy.
    """
    raw = consume_snapshots(topic, bootstrap_servers)
    raw.sort(key=lambda r: r['ts_event'])
    total_cash = 0.0
    remaining = order_size

    for rec in raw:
        if remaining <= 0:
            break
        trade = min(remaining, rec['ask_sz'])
        total_cash += trade * rec['ask_px']
        remaining  -= trade

    avg_fill_px = total_cash / order_size
    return {'total_cash': total_cash, 'avg_fill_px': avg_fill_px}

def backtest_twap(order_size, topic, bootstrap_servers):
    """
    Backtest the TWAP strategy.
    """
    raw = consume_snapshots(topic, bootstrap_servers)
    raw.sort(key=lambda r: r['ts_event'])

    start = raw[0]['ts_event']
    end   = raw[-1]['ts_event']
    window = timedelta(seconds=60)
    buckets = []
    t = start
    while t < end:
        buckets.append((t, t + window))
        t += window

    slice_size = order_size / len(buckets)
    total_cash = 0.0

    for (t0, t1) in buckets:
        bucket = [r for r in raw if t0 <= r['ts_event'] < t1]
        bucket.sort(key=lambda r: r['ask_px'])
        rem = slice_size
        for rec in bucket:
            if rem <= 0:
                break
            trade = min(rem, rec['ask_sz'])
            total_cash += trade * rec['ask_px']
            rem -= trade

    avg_fill_px = total_cash / order_size
    return {'total_cash': total_cash, 'avg_fill_px': avg_fill_px}

def backtest_vwap(order_size, topic, bootstrap_servers):
    """
    Backtest the VWAP strategy.
    """
    raw = consume_snapshots(topic, bootstrap_servers)
    raw.sort(key=lambda r: r['ts_event'])
    total_display = sum(r['ask_sz'] for r in raw)
    total_cash = 0.0
    remaining = order_size

    for rec in raw:
        if remaining <= 0:
            break
 
        alloc = order_size * (rec['ask_sz'] / total_display)
        trade = min(remaining, alloc)
        total_cash += trade * rec['ask_px']
        remaining  -= trade

    avg_fill_px = total_cash / order_size
    return {'total_cash': total_cash, 'avg_fill_px': avg_fill_px}

def compute_savings(sor_px, baseline_px):
    """
    Computes the savings in basis points.
    """
    return (baseline_px - sor_px) / baseline_px * 10000

if __name__ == "__main__":
    topic = 'mock_l1_stream'
    bootstrap = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
    order_size = 5000
    lambda_over_choices  = [0.1, 0.2, 0.3, 0.4, 0.5]
    lambda_under_choices = [0.1, 0.2, 0.4, 0.6, 0.8]
    theta_queue_choices  = [0.1, 0.2, 0.3, 0.5]

    best = search(lambda_over_choices, lambda_under_choices, theta_queue_choices, order_size, topic, bootstrap)
    lo, lu, tq = best['params']
    best_ask = backtest_best_ask(order_size, topic, bootstrap)
    twap     = backtest_twap(order_size, topic, bootstrap)
    vwap     = backtest_vwap(order_size, topic, bootstrap)

    sor_px = best['avg_fill_px']
    savings = {
        'best_ask': compute_savings(sor_px, best_ask['avg_fill_px']),
        'twap':     compute_savings(sor_px, twap['avg_fill_px']),
        'vwap':     compute_savings(sor_px, vwap['avg_fill_px'])
    }

    output = {
      "best_parameters": {
        "lambda_over": lo,
        "lambda_under": lu,
        "theta_queue": tq
      },
      "optimized": {
        "total_cash": best['total_cash'],
        "avg_fill_px": best['avg_fill_px']
      },
      "baselines": {
        "best_ask": best_ask,
        "twap":     twap,
        "vwap":     vwap
      },
      "savings_vs_baselines_bps": savings
    }

    print(json.dumps(output, indent=2))
