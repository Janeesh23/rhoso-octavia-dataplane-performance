#!/usr/bin/env python3
"""
Scrape Prometheus metrics from an Octavia amphora's PROMETHEUS protocol listener.
Outputs timestamped CSV with amphora CPU, memory, backend connect time, and
HTTP response code rates.

The Octavia PROMETHEUS listener exposes metrics in the 'octavia_*' namespace,
NOT raw haproxy_* metrics.

Usage:
    python3 scrape-amphora-prometheus.py http://<vip>:8088/metrics [interval_seconds]

Output columns:
    timestamp,cpu_pct,memory_pct,backend_connect_time_ms,http_2xx,http_4xx,http_5xx

Where:
    cpu_pct = octavia_loadbalancer_cpu (load_avg_1m / cpu_count * 100)
              100% means all amphora vCPUs are fully loaded.
    memory_pct = octavia_loadbalancer_memory (psutil.virtual_memory().percent)
              Percentage of amphora VM's total RAM in use.
"""

import re
import sys
import time
import urllib.request
import urllib.error

GAUGE_PATTERNS = {
    'cpu_pct': re.compile(
        r'^octavia_loadbalancer_cpu\s+(\d+\.?\d*)', re.MULTILINE),
    'memory_pct': re.compile(
        r'^octavia_loadbalancer_memory\s+(\d+\.?\d*)', re.MULTILINE),
    'backend_connect_time_s': re.compile(
        r'^octavia_pool_connect_time_average_seconds\b.*?\s+(\d+\.?\d*)', re.MULTILINE),
}

HTTP_RESPONSE_PATTERN = re.compile(
    r'^octavia_listener_http_responses_total\{.*?code="(\d)xx".*?\}\s+(\d+\.?\d*)', re.MULTILINE)


def scrape_once(url):
    """Fetch metrics from endpoint and parse key values."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'amphora-scraper/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode('utf-8', errors='replace')
    except (urllib.error.URLError, OSError) as e:
        print(f"# scrape error: {e}", file=sys.stderr)
        return None

    result = {}

    for name, pattern in GAUGE_PATTERNS.items():
        matches = pattern.findall(body)
        if matches:
            result[name] = sum(float(m) for m in matches)
        else:
            result[name] = 0.0

    http_codes = {'http_2xx': 0.0, 'http_4xx': 0.0, 'http_5xx': 0.0}
    for code_prefix, value in HTTP_RESPONSE_PATTERN.findall(body):
        key = f'http_{code_prefix}xx'
        if key in http_codes:
            http_codes[key] += float(value)
    result.update(http_codes)

    return result


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <metrics_url> [interval_seconds]", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    columns = [
        'timestamp', 'cpu_pct', 'memory_pct',
        'backend_connect_time_ms', 'http_2xx', 'http_4xx', 'http_5xx'
    ]
    print(','.join(columns))
    sys.stdout.flush()

    prev_counters = None
    prev_time = None

    while True:
        now = time.time()
        data = scrape_once(url)
        if data is None:
            time.sleep(interval)
            continue

        timestamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(now))

        if prev_counters is not None and prev_time is not None:
            dt = now - prev_time
            if dt > 0:
                row = {
                    'timestamp': timestamp,
                    'cpu_pct': data['cpu_pct'],
                    'memory_pct': data['memory_pct'],
                    'backend_connect_time_ms': data['backend_connect_time_s'] * 1000,
                    'http_2xx': (data['http_2xx'] - prev_counters['http_2xx']) / dt,
                    'http_4xx': (data['http_4xx'] - prev_counters['http_4xx']) / dt,
                    'http_5xx': (data['http_5xx'] - prev_counters['http_5xx']) / dt,
                }
                values = [row.get(c, 0) for c in columns]
                print(','.join(str(v) if isinstance(v, str) else f'{v:.4f}' for v in values))
                sys.stdout.flush()

        prev_counters = data
        prev_time = now
        time.sleep(interval)


if __name__ == '__main__':
    main()
