#!/usr/bin/env python3
"""
Generate an interactive HTML performance report combining:
  - Benchmark results (summary.txt)
  - Compute node (hypervisor) metrics: CPU, memory, load
  - Amphora VM metrics (HAProxy via Prometheus): sessions, req/s, CPU, memory

Usage:
    python3 generate-report.py results/amphora-https-20260526-012106/
    python3 generate-report.py results/amphora-https-20260526-012106/ -o report.html

The metrics/ subdirectory should contain per-test CSV files:
    compute_<test_name>_run<N>.csv  — compute node CPU/memory/load
    amphora_<test_name>_run<N>.csv  — amphora HAProxy prometheus metrics
"""

import argparse
import csv
import html
import math
import os
import re
import sys


def load_summary_txt(results_dir):
    summary_path = os.path.join(results_dir, 'summary.txt')
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            return f.read()
    return None


def load_metrics_file(filepath):
    samples = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            sample = {}
            for k, v in row.items():
                if k is None:
                    continue
                k = k.strip()
                try:
                    sample[k] = float(v)
                except (ValueError, TypeError):
                    sample[k] = v
            samples.append(sample)
    return samples


def load_all_metrics(metrics_dir):
    """Load metrics CSVs grouped by source (compute/amphora), test name, and run.

    Returns: {
        'compute': { test_name: { run_num: [samples] } },
        'amphora': { test_name: { run_num: [samples] } }
    }
    """
    result = {'compute': {}, 'amphora': {}}
    if not metrics_dir or not os.path.isdir(metrics_dir):
        return result

    for f in sorted(os.listdir(metrics_dir)):
        if not f.endswith('.csv'):
            continue
        basename = f[:-4]

        # Match: compute_<test>_run<N> or amphora_<test>_run<N>
        m = re.match(r'^(compute|amphora)_(.+)_run(\d+)$', basename)
        if m:
            source = m.group(1)
            test_name = m.group(2)
            run_num = int(m.group(3))
        else:
            # Fallback: compute_<test> or amphora_<test>
            m2 = re.match(r'^(compute|amphora)_(.+)$', basename)
            if m2:
                source = m2.group(1)
                test_name = m2.group(2)
                run_num = 1
            else:
                continue

        filepath = os.path.join(metrics_dir, f)
        samples = load_metrics_file(filepath)
        if samples:
            result[source].setdefault(test_name, {})[run_num] = samples

    return result


def compute_run_average(samples):
    if not samples:
        return {}
    numeric_keys = [k for k in samples[0] if isinstance(samples[0].get(k), (int, float))]
    averages = {}
    for key in numeric_keys:
        values = [s[key] for s in samples if isinstance(s.get(key), (int, float))]
        if values:
            averages[key] = sum(values) / len(values)
    return averages


def compute_summary_across_runs(runs_dict):
    """For each metric, average within each run, then compute stats across runs."""
    if not runs_dict:
        return {}
    per_run_avgs = []
    for run_num in sorted(runs_dict.keys()):
        avg = compute_run_average(runs_dict[run_num])
        if avg:
            per_run_avgs.append(avg)
    if not per_run_avgs:
        return {}

    all_keys = set()
    for a in per_run_avgs:
        all_keys.update(a.keys())

    summary = {}
    for key in sorted(all_keys):
        values = [r[key] for r in per_run_avgs if key in r]
        if not values:
            continue
        n = len(values)
        avg = sum(values) / n
        std_dev = math.sqrt(sum((x - avg) ** 2 for x in values) / (n - 1)) if n > 1 else 0.0
        cv_pct = (std_dev / avg * 100) if avg != 0 else 0.0
        summary[key] = {
            'avg': avg, 'min': min(values), 'max': max(values),
            'std_dev': std_dev, 'cv_pct': cv_pct, 'runs': n, 'per_run': values,
        }
    return summary


def build_metrics_table_html(test_name, summary, display_fields):
    """Render a summary table for one test."""
    num_runs = next((v.get('runs', 0) for v in summary.values()), 0)
    h = f'<h4>{html.escape(test_name)} ({num_runs} run{"s" if num_runs != 1 else ""})</h4>\n'
    h += '<table class="metrics-table">\n'
    h += '<tr><th>Metric</th><th>Avg</th><th>Min</th><th>Max</th><th>Std Dev</th><th>CV %</th>'
    for r in range(1, num_runs + 1):
        h += f'<th>Run {r}</th>'
    h += '</tr>\n'
    for key, label in display_fields:
        if key not in summary:
            continue
        s = summary[key]
        h += f'<tr><td>{label}</td>'
        h += f'<td>{s["avg"]:.2f}</td><td>{s["min"]:.2f}</td><td>{s["max"]:.2f}</td>'
        h += f'<td>{s["std_dev"]:.2f}</td><td>{s["cv_pct"]:.2f}</td>'
        for v in s.get('per_run', []):
            h += f'<td>{v:.2f}</td>'
        h += '</tr>\n'
    h += '</table>\n'
    return h


def build_compute_table_html(test_name, summary, display_fields, num_cpus):
    """Render compute CPU table in absolute format: State | Max% | ~N CPUs busy."""
    num_runs = next((v.get('runs', 0) for v in summary.values()), 0)
    h = f'<h4>{html.escape(test_name)} ({num_runs} run{"s" if num_runs != 1 else ""}'
    if num_cpus:
        h += f', {num_cpus} CPUs on host'
    h += ')</h4>\n'

    cpu_fields = [f for f in display_fields if f[0].startswith('cpu_')]
    mem_fields = [f for f in display_fields if not f[0].startswith('cpu_')]

    if cpu_fields:
        h += '<table class="metrics-table">\n'
        h += '<tr><th>CPU State</th><th>Avg (%)</th><th>Max (%)</th>'
        h += '<th>~CPUs (avg)</th><th>~CPUs (max)</th>'
        for r in range(1, num_runs + 1):
            h += f'<th>Run {r} (avg)</th>'
        h += '</tr>\n'
        for key, label in cpu_fields:
            if key not in summary:
                continue
            s = summary[key]
            avg_pct = s['avg']
            max_pct = s['max']
            cpus_avg = avg_pct / 100.0
            cpus_max = max_pct / 100.0
            h += f'<tr><td>{label}</td>'
            h += f'<td>{avg_pct:.1f}%</td><td>{max_pct:.1f}%</td>'
            h += f'<td>~{cpus_avg:.2f}</td><td>~{cpus_max:.2f}</td>'
            for v in s.get('per_run', []):
                h += f'<td>{v:.1f}%</td>'
            h += '</tr>\n'
        h += '</table>\n'

    if mem_fields:
        h += '<table class="metrics-table">\n'
        h += '<tr><th>Metric</th><th>Avg</th><th>Min</th><th>Max</th><th>Std Dev</th><th>CV %</th>'
        for r in range(1, num_runs + 1):
            h += f'<th>Run {r}</th>'
        h += '</tr>\n'
        for key, label in mem_fields:
            if key not in summary:
                continue
            s = summary[key]
            h += f'<tr><td>{label}</td>'
            h += f'<td>{s["avg"]:.2f}</td><td>{s["min"]:.2f}</td><td>{s["max"]:.2f}</td>'
            h += f'<td>{s["std_dev"]:.2f}</td><td>{s["cv_pct"]:.2f}</td>'
            for v in s.get('per_run', []):
                h += f'<td>{v:.2f}</td>'
            h += '</tr>\n'
        h += '</table>\n'

    return h


def generate_html(results_dir, output_path):
    workload_name = os.path.basename(os.path.normpath(results_dir))
    summary_txt = load_summary_txt(results_dir)

    metrics_dir = os.path.join(results_dir, 'metrics')
    all_metrics = load_all_metrics(metrics_dir)

    compute_tables_html = ""
    compute_fields = [
        ('cpu_user', 'CPU User'), ('cpu_system', 'CPU System'),
        ('cpu_softirq', 'CPU SoftIRQ'), ('cpu_irq', 'CPU IRQ'),
        ('cpu_iowait', 'CPU IOWait'), ('cpu_idle', 'CPU Idle'),
        ('cpu_total', 'CPU Total (busy)'),
        ('mem_used_pct', 'Memory Used %'), ('mem_used_mb', 'Memory Used (MB)'),
        ('mem_available_mb', 'Memory Available (MB)'),
    ]
    if all_metrics['compute']:
        for test_name in sorted(all_metrics['compute'].keys()):
            runs_dict = all_metrics['compute'][test_name]
            summary = compute_summary_across_runs(runs_dict)
            num_cpus = 0
            for run_samples in runs_dict.values():
                if run_samples and 'num_cpus' in run_samples[0]:
                    num_cpus = int(run_samples[0]['num_cpus'])
                    break
            compute_tables_html += build_compute_table_html(
                test_name, summary, compute_fields, num_cpus)
    else:
        compute_tables_html = '<p class="no-data">No compute node metrics found.</p>'

    amphora_tables_html = ""
    amphora_fields = [
        ('cpu_pct', 'Amphora CPU %'), ('memory_pct', 'Amphora Memory %'),
        ('backend_connect_time_ms', 'Backend Connect Time (ms)'),
        ('http_2xx', 'HTTP 2xx/s'), ('http_4xx', 'HTTP 4xx/s'), ('http_5xx', 'HTTP 5xx/s'),
    ]
    if all_metrics['amphora']:
        for test_name in sorted(all_metrics['amphora'].keys()):
            runs_dict = all_metrics['amphora'][test_name]
            summary = compute_summary_across_runs(runs_dict)
            amphora_tables_html += build_metrics_table_html(test_name, summary, amphora_fields)
    else:
        amphora_tables_html = '<p class="no-data">No amphora VM metrics found.</p>'

    summary_txt_escaped = html.escape(summary_txt) if summary_txt else "No summary.txt found."

    report = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Performance Report — {html.escape(workload_name)}</title>
<style>
:root {{ --bg:#1a1a2e; --surface:#16213e; --surface2:#0f3460; --accent:#e94560;
  --accent2:#0ea5e9; --text:#e0e0e0; --muted:#a0a0b0; --border:#2a2a4a;
  --green:#10b981; --yellow:#f59e0b; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg);
  color:var(--text); line-height:1.6; padding:2rem; }}
.container {{ max-width:1400px; margin:0 auto; }}
h1 {{ font-size:2rem; color:var(--accent2); border-bottom:2px solid var(--accent); padding-bottom:.5rem; margin-bottom:.5rem; }}
h2 {{ font-size:1.4rem; margin:2rem 0 1rem; color:var(--accent2); cursor:pointer; user-select:none; }}
h2:hover {{ color:var(--accent); }}
h2::before {{ content:"\\25B8 "; font-size:.9rem; }}
h2.expanded::before {{ content:"\\25BE "; }}
h3 {{ font-size:1.2rem; margin:1.5rem 0 .5rem; color:var(--accent2); }}
h4 {{ font-size:1rem; margin:1.2rem 0 .4rem; color:var(--text); }}
.section {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:1.5rem; margin-bottom:1.5rem; }}
.section-content {{ display:none; }}
.section-content.visible {{ display:block; }}
pre {{ background:var(--surface2); border:1px solid var(--border); border-radius:6px;
  padding:1rem; overflow-x:auto; font-family:'JetBrains Mono','Fira Code',monospace;
  font-size:.8rem; line-height:1.4; white-space:pre; }}
.metrics-table {{ width:100%; border-collapse:collapse; margin:.5rem 0 1.5rem; font-size:.85rem; }}
.metrics-table th,.metrics-table td {{ padding:.4rem .8rem; text-align:right; border-bottom:1px solid var(--border); }}
.metrics-table th {{ background:var(--surface2); color:var(--accent2); font-weight:600; }}
.metrics-table td:first-child,.metrics-table th:first-child {{ text-align:left; }}
.metrics-table tr:hover td {{ background:rgba(14,165,233,.05); }}
.no-data {{ color:var(--muted); font-style:italic; padding:1rem; }}
.subtitle {{ color:var(--muted); font-size:.9rem; margin-bottom:1.5rem; }}
.run-count {{ color:var(--muted); font-weight:normal; font-size:.85rem; }}
@media(max-width:900px) {{ body {{ padding:1rem; }} }}
</style>
</head>
<body>
<div class="container">
<h1>Octavia Amphora Performance Report</h1>
<p class="subtitle">Workload: <strong>{html.escape(workload_name)}</strong></p>

<div class="section">
<h2 class="expanded" onclick="toggleSection(this)">Benchmark Results (summary.txt)</h2>
<div class="section-content visible">
<pre>{summary_txt_escaped}</pre>
</div>
</div>

<div class="section">
<h2 class="expanded" onclick="toggleSection(this)">Compute Node (Hypervisor) Resource Usage</h2>
<div class="section-content visible">
{compute_tables_html}
</div>
</div>

<div class="section">
<h2 class="expanded" onclick="toggleSection(this)">Amphora VM (HAProxy) Metrics</h2>
<div class="section-content visible">
{amphora_tables_html}
</div>
</div>
</div>

<script>
function toggleSection(h2) {{
    h2.classList.toggle('expanded');
    h2.nextElementSibling.classList.toggle('visible');
}}
</script>
</body>
</html>'''

    with open(output_path, 'w') as f:
        f.write(report)
    print(f"Report written to {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='Generate HTML performance report from benchmark results and metrics')
    parser.add_argument('results_dir',
                        help='Path to results directory (contains summary.txt, run*/, metrics/)')
    parser.add_argument('--output', '-o', default=None,
                        help='Output HTML file path (default: <results_dir>/report.html)')
    args = parser.parse_args()

    results_dir = os.path.expanduser(args.results_dir)
    if not os.path.isdir(results_dir):
        print(f"Error: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or os.path.join(results_dir, 'report.html')
    generate_html(results_dir, output_path)


if __name__ == '__main__':
    main()
