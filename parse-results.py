#!/usr/bin/env python3
"""
Parse h2load and wrk benchmark results from run directories and produce
a summary table with avg, max, min, and std_dev for req/s and bandwidth.

Usage:
    python3 parse-results.py ~/results/amphora-http-20260521-023000
    python3 parse-results.py ~/results/amphora-http-20260521-023000 -o summary
"""

import argparse
import csv
import math
import os
import re
import sys

h2load_summary_re = re.compile(
    r'finished in \d+\.\d+s, ([0-9.]+) req/s, ([0-9.]+)([MGK]?B/s)')

h2load_rtt_re = re.compile(
    r'time to 1st byte:\s+([0-9.]+)([um]?s)\s+([0-9.]+)([um]?s)\s+'
    r'([0-9.]+)([um]?s)\s+([0-9.]+)([um]?s)\s+([0-9.]+)%')

wrk_req_re = re.compile(r'^Requests/sec:\s+([0-9.]+)$', re.MULTILINE)
wrk_bw_re = re.compile(r'^Transfer/sec:\s+([0-9.]+)([GMK]?B)$', re.MULTILINE)


def parse_bandwidth(value, unit):
    """Convert bandwidth to Mbits/s."""
    bw = float(value)
    if unit in ('GB/s', 'GB'):
        return bw * 8 * 1024
    elif unit in ('MB/s', 'MB'):
        return bw * 8
    elif unit in ('KB/s', 'KB'):
        return bw * 8 / 1024
    elif unit in ('B/s', 'B'):
        return bw * 8 / 1024 / 1024
    raise ValueError(f"Unknown unit: {unit}")


def parse_rtt_to_us(value, unit):
    """Convert RTT to microseconds."""
    v = float(value)
    if unit == 's':
        return v * 1_000_000
    elif unit == 'ms' or unit == 'm':
        return v * 1000
    return v


def parse_log_file(filepath):
    """Parse a single log file and return extracted metrics."""
    with open(filepath) as f:
        content = f.read()

    if not content.strip():
        print(f"WARNING: Empty log file: {filepath}", file=sys.stderr)
        return None

    result = {}

    m = h2load_summary_re.search(content)
    if m:
        result['req_s'] = float(m.group(1))
        result['bandwidth_mbps'] = parse_bandwidth(m.group(2), m.group(3))

        rtt_m = h2load_rtt_re.search(content)
        if rtt_m:
            g = rtt_m.groups()
            result['rtt_min_us'] = parse_rtt_to_us(g[0], g[1])
            result['rtt_max_us'] = parse_rtt_to_us(g[2], g[3])
            result['rtt_mean_us'] = parse_rtt_to_us(g[4], g[5])
            result['rtt_sd_us'] = parse_rtt_to_us(g[6], g[7])
        return result

    req_m = wrk_req_re.search(content)
    bw_m = wrk_bw_re.search(content)
    if req_m:
        result['req_s'] = float(req_m.group(1))
    if bw_m:
        result['bandwidth_mbps'] = parse_bandwidth(bw_m.group(1), bw_m.group(2))

    return result if result else None


def compute_stats(values):
    """Compute min, max, avg, std_dev, and std_dev_pct for a list of values."""
    if not values:
        return {'min': 'N/A', 'max': 'N/A', 'avg': 'N/A', 'std_dev': 'N/A',
                'std_dev_pct': 'N/A'}
    n = len(values)
    avg = sum(values) / n
    if n > 1:
        variance = sum((x - avg) ** 2 for x in values) / (n - 1)
        std_dev = math.sqrt(variance)
    else:
        std_dev = 0.0
    std_dev_pct = (std_dev / avg * 100) if avg != 0 else 0.0
    return {
        'min': round(min(values), 2),
        'max': round(max(values), 2),
        'avg': round(avg, 2),
        'std_dev': round(std_dev, 2),
        'std_dev_pct': round(std_dev_pct, 2),
    }


def discover_runs(results_dir):
    """Find all run directories and test log files."""
    runs = {}
    for entry in sorted(os.listdir(results_dir)):
        run_path = os.path.join(results_dir, entry)
        if os.path.isdir(run_path) and entry.startswith('run'):
            run_num = entry
            runs[run_num] = {}
            for log_file in sorted(os.listdir(run_path)):
                if log_file.endswith('.log'):
                    test_name = log_file[:-4]
                    runs[run_num][test_name] = os.path.join(run_path, log_file)
    return runs


def format_num(v, width=12):
    """Format a number for table display."""
    if isinstance(v, str):
        return v.rjust(width)
    return f"{v:,.2f}".rjust(width)


def print_table(title, headers, rows, out=sys.stdout):
    """Print a formatted table with box-drawing characters."""
    col_widths = []
    for i, h in enumerate(headers):
        w = len(h)
        for row in rows:
            if i < len(row):
                cell = f"{row[i]:,.2f}" if isinstance(row[i], (int, float)) else str(row[i])
                w = max(w, len(cell))
        col_widths.append(w)

    def fmt_row(row):
        cells = []
        for i, val in enumerate(row):
            cell = f"{val:,.2f}" if isinstance(val, (int, float)) else str(val)
            if i == 0:
                cells.append(cell.ljust(col_widths[i]))
            elif i == 1 and len(headers) > 6:
                cells.append(cell.center(col_widths[i]))
            else:
                cells.append(cell.rjust(col_widths[i]))
        return " │ ".join(cells)

    top    = "─┬─".join("─" * w for w in col_widths)
    sep    = "─┼─".join("─" * w for w in col_widths)
    bottom = "─┴─".join("─" * w for w in col_widths)

    header_cells = []
    for i, h in enumerate(headers):
        if i == 0:
            header_cells.append(h.ljust(col_widths[i]))
        elif i == 1 and len(headers) > 6:
            header_cells.append(h.center(col_widths[i]))
        else:
            header_cells.append(h.rjust(col_widths[i]))
    header_line = " │ ".join(header_cells)

    if title:
        out.write(f"\n  {title}\n")
    out.write(f"┌─{top}─┐\n")
    out.write(f"│ {header_line} │\n")
    out.write(f"├─{sep}─┤\n")
    for row in rows:
        out.write(f"│ {fmt_row(row)} │\n")
    out.write(f"└─{bottom}─┘\n")


def write_csv(workload_name, runs, all_test_names, test_data, out):
    """Write results in CSV format."""
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow([f'Workload: {workload_name}', f'Runs: {len(runs)}'])
    writer.writerow([])
    writer.writerow([
        'test', 'metric',
        'avg', 'min', 'max', 'std_dev', 'cv_pct',
        *[f'run{i+1}' for i in range(len(runs))]
    ])
    for test_name in all_test_names:
        data = test_data[test_name]
        for metric, unit in [('req_s', 'req/s'), ('bandwidth_mbps', 'Mbps')]:
            values = data[metric]
            if not values:
                continue
            stats = compute_stats(values)
            writer.writerow([
                test_name, unit,
                stats['avg'], stats['min'], stats['max'], stats['std_dev'],
                stats['std_dev_pct'],
                *[round(v, 2) for v in values]
            ])
    writer.writerow([])
    writer.writerow(['--- Per-test summary (req/s) ---'])
    writer.writerow(['test', 'avg', 'min', 'max', 'std_dev', 'cv_pct'])
    for test_name in all_test_names:
        values = test_data[test_name]['req_s']
        if values:
            stats = compute_stats(values)
            writer.writerow([test_name, stats['avg'], stats['min'],
                             stats['max'], stats['std_dev'], stats['std_dev_pct']])
    writer.writerow([])
    writer.writerow(['--- Per-test summary (bandwidth Mbps) ---'])
    writer.writerow(['test', 'avg', 'min', 'max', 'std_dev', 'cv_pct'])
    for test_name in all_test_names:
        values = test_data[test_name]['bandwidth_mbps']
        if values:
            stats = compute_stats(values)
            writer.writerow([test_name, stats['avg'], stats['min'],
                             stats['max'], stats['std_dev'], stats['std_dev_pct']])


def write_tables(workload_name, runs, all_test_names, test_data, out):
    """Write results as formatted tables to stdout."""
    num_runs = len(runs)
    out.write(f"\n  Workload: {workload_name}  |  Runs: {num_runs}\n")

    headers = ['Test', 'Metric', 'Avg', 'Min', 'Max', 'Std Dev', 'CV %',
               *[f'Run {i+1}' for i in range(num_runs)]]
    rows = []
    for test_name in all_test_names:
        data = test_data[test_name]
        for metric, unit in [('req_s', 'req/s'), ('bandwidth_mbps', 'Mbps')]:
            values = data[metric]
            if not values:
                continue
            stats = compute_stats(values)
            rows.append([
                test_name, unit,
                stats['avg'], stats['min'], stats['max'], stats['std_dev'],
                stats['std_dev_pct'],
                *[round(v, 2) for v in values]
            ])
    print_table("Detailed Results", headers, rows, out)

    summary_headers = ['Test', 'Avg', 'Min', 'Max', 'Std Dev', 'CV %']

    reqs_rows = []
    for test_name in all_test_names:
        values = test_data[test_name]['req_s']
        if values:
            stats = compute_stats(values)
            reqs_rows.append([test_name, stats['avg'], stats['min'],
                              stats['max'], stats['std_dev'], stats['std_dev_pct']])
    print_table("Per-test Summary (req/s)", summary_headers, reqs_rows, out)

    bw_rows = []
    for test_name in all_test_names:
        values = test_data[test_name]['bandwidth_mbps']
        if values:
            stats = compute_stats(values)
            bw_rows.append([test_name, stats['avg'], stats['min'],
                            stats['max'], stats['std_dev'], stats['std_dev_pct']])
    print_table("Per-test Summary (Bandwidth Mbps)", summary_headers, bw_rows, out)


def main():
    parser = argparse.ArgumentParser(
        description='Parse benchmark results and generate summary')
    parser.add_argument('results_dir', help='Path to results directory')
    parser.add_argument('--output', '-o', default=None,
                        help='Output file path (writes both table and CSV)')
    parser.add_argument('--format', choices=['table', 'csv'], default='table',
                        help='Output format for stdout (default: table)')
    args = parser.parse_args()

    results_dir = os.path.expanduser(args.results_dir)
    if not os.path.isdir(results_dir):
        print(f"Error: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    runs = discover_runs(results_dir)
    if not runs:
        print(f"Error: No run directories found in {results_dir}", file=sys.stderr)
        sys.exit(1)

    workload_name = os.path.basename(results_dir)
    all_test_names = sorted({t for r in runs.values() for t in r.keys()})

    test_data = {}
    for test_name in all_test_names:
        test_data[test_name] = {
            'req_s': [],
            'bandwidth_mbps': [],
            'rtt_min_us': [],
            'rtt_max_us': [],
            'rtt_mean_us': [],
            'rtt_sd_us': [],
        }

    parse_failures = []
    for run_num, tests in runs.items():
        for test_name, log_path in tests.items():
            parsed = parse_log_file(log_path)
            if parsed:
                for metric, value in parsed.items():
                    if metric in test_data[test_name]:
                        test_data[test_name][metric].append(value)
            else:
                parse_failures.append(f"{run_num}/{test_name}")
                print(f"WARNING: Could not parse {log_path}", file=sys.stderr)

    if parse_failures:
        print(f"WARNING: {len(parse_failures)} log(s) could not be parsed: "
              f"{', '.join(parse_failures)}", file=sys.stderr)

    if args.format == 'csv':
        write_csv(workload_name, runs, all_test_names, test_data, sys.stdout)
    else:
        write_tables(workload_name, runs, all_test_names, test_data, sys.stdout)

    if args.output:
        base, ext = os.path.splitext(args.output)

        table_path = base + '.txt'
        with open(table_path, 'w') as f:
            write_tables(workload_name, runs, all_test_names, test_data, f)
        print(f"Table written to {table_path}", file=sys.stderr)

        csv_path = base + '.csv'
        with open(csv_path, 'w', newline='') as f:
            write_csv(workload_name, runs, all_test_names, test_data, f)
        print(f"CSV written to {csv_path}", file=sys.stderr)


if __name__ == '__main__':
    main()
