# RHOSO Octavia Dataplane Performance

Ansible automation for benchmarking Red Hat OpenStack Services on OpenShift (RHOSO) Octavia load balancer dataplane performance across multiple protocols and configurations.

## Prerequisites

- **Python 3.6+** — Required for `parse-results.py`, `generate-report.py`, and `scrape-amphora-prometheus.py`
- **Ansible 2.14+** — With required collections installed (see below)
- **OpenStack CLI** configured (`~/.config/openstack/clouds.yaml` with a `default` cloud entry)
- **`oc`** access to the OpenShift cluster running RHOSO
- **SSH access** from the machine running the playbooks (bastion host) to the OpenStack tenant networks, so Ansible can reach the client and member VMs via their floating IPs

Install Ansible collection dependencies:

```bash
ansible-galaxy collection install -r requirements.yml
```

## Project Structure

```
├── ansible.cfg                   # Ansible configuration (pipelining, timeout, callbacks)
├── requirements.yml              # Ansible Galaxy collection dependencies
├── setup-infrastructure.yaml     # Full infrastructure setup
├── cleanup-infrastructure.yaml   # Full infrastructure teardown
├── setup-loadbalancers.yaml      # Create LB test scenarios only
├── run-workloads.yaml            # Run performance workloads
├── generate-inventory.yaml       # Generate static inventory file
├── parse-results.py              # Parse benchmark logs into table/CSV
├── generate-report.py            # Generate HTML performance report
├── collect-compute-metrics.sh    # Hypervisor CPU/memory collector (runs on compute node)
├── scrape-amphora-prometheus.py  # Amphora Prometheus metrics scraper (runs on client VM)
├── group_vars/
│   └── all.yml                   # Global variables (LB name, flavor, AZ, etc.)
└── roles/
    ├── create-network/           # Create tenant networks, subnets, router
    ├── create-az/                # Create host aggregates and Octavia AZ
    ├── create-flavor/            # Create Nova + Octavia LB flavors
    ├── prepare-image/            # Upload VM image to Glance
    ├── client/                   # Create client VMs with floating IPs
    ├── member/                   # Create member (backend) VMs with floating IPs
    ├── build-tools/              # Install h2load, wrk, nginx, iperf on VMs
    ├── cert-generation/          # Generate TLS certificates
    ├── barbican-secret/          # Store TLS certs in Barbican
    ├── amphora-http/             # Create HTTP load balancer
    ├── amphora-http-503/         # Create HTTP load balancer with empty pool (503 baseline)
    ├── amphora-tcp/              # Create TCP load balancer
    ├── amphora-https/            # Create HTTPS (TLS termination) load balancer
    ├── amphora-https-reencryption/ # Create HTTPS re-encryption load balancer
    ├── amphora-udp/              # Create UDP load balancer
    ├── run-workload/             # Generic workload runner (used by run-workloads.yaml)
    ├── collect-metrics/          # Collect hypervisor + amphora metrics during tests
    └── cleanup/                  # Tear down all resources
```

## Execution Flow

### Step 1: Setup Infrastructure

Creates networks, availability zones, flavors, VMs, and installs benchmarking tools:

```bash
ansible-playbook setup-infrastructure.yaml
```

This runs the following in order:

1. **Install python-openstackclient** — Ensures the OpenStack CLI is available
2. **create-network** — Tenant networks (vip, member, public), subnets, router
3. **create-az** — Host aggregates and Octavia availability zone
4. **prepare-image** — Upload CentOS 9 Stream image
5. **client** — Create client VM (kakashi-client1) with floating IP
6. **member** — Create member VMs (naruto-member1, sasuke-member2, sakura-member3) with floating IPs
7. **cert-generation** — Generate member TLS certificates (`/tmp/member_certs`)
8. **cert-generation** — Generate LB TLS certificates (`/tmp/lb_certs`)
9. **barbican-secret** — Store TLS secret in Barbican
10. **create-flavor** — Create Nova compute flavors and Octavia LB flavors
11. **Build dynamic inventory** — Discover floating IPs, add hosts, and write `~/.ssh/config` entries
12. **Wait for SSH** — Wait up to 300s for all VMs to become reachable
13. **build-tools** — Install h2load, wrk, nginx, iperf, httpterm on all VMs

### Step 2: Generate Inventory

Creates a static inventory file for running workloads against the client/member VMs:

```bash
ansible-playbook generate-inventory.yaml
```

Produces an `inventory` file:

```ini
[client]
kakashi-client1 ansible_host=172.19.0.x

[members]
naruto-member1 ansible_host=172.19.0.x
sasuke-member2 ansible_host=172.19.0.x
sakura-member3 ansible_host=172.19.0.x

[all:vars]
ansible_user=cloud-user
ansible_ssh_private_key_file=~/.ssh/id_rsa.me
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
```

### Optional: Pre-create Load Balancers

`setup-loadbalancers.yaml` creates load balancers without running any benchmarks. This is useful for pre-provisioning or validating LB creation independently:

```bash
ansible-playbook setup-loadbalancers.yaml
```

Create a specific LB type only:

```bash
ansible-playbook setup-loadbalancers.yaml --tags amphora-https
```

> **Note:** This step is optional. `run-workloads.yaml` (Step 3) automatically creates the required load balancer before each workload run.

### Step 3: Run Workloads

Each workload creates its load balancer, then runs benchmarks from the client VM. Results are saved to `~/results/<workload_name>/` on the client.

**Run all workloads:**

```bash
ansible-playbook -i inventory run-workloads.yaml
```

**Run a specific workload:**

```bash
ansible-playbook -i inventory run-workloads.yaml --tags amphora-http
```

**Run with metrics collection enabled:**

```bash
ansible-playbook -i inventory run-workloads.yaml --tags amphora-http \
  -e collect_metrics=true -e num_runs=3
```

When `collect_metrics=true`, the framework collects:
- **Compute node (hypervisor) metrics** — CPU and memory usage from the hypervisor hosting the amphora VM, sampled every `metrics_interval` seconds
- **Amphora VM metrics** — CPU, memory, HTTP response rates, and backend connect time scraped from the amphora's Prometheus endpoint

An HTML report is automatically generated at the end of each run at `results/<workload>/report.html`.

#### Available Workload Tags

| Tag | LB Type | Protocol | Tests |
|---|---|---|---|
| `amphora-http` | HTTP | HTTP/1.1 | h2load (0b), wrk (128b–1m) |
| `amphora-http2-clear` | HTTP (reused) | HTTP/2 cleartext | h2load (0b–1m) |
| `amphora-http-503` | HTTP (empty pool) | HTTP/1.1 | h2load (0b only — 503 baseline) |
| `amphora-tcp` | TCP | TCP passthrough | h2load (0b), wrk (128b–1m) |
| `amphora-https` | HTTPS (TLS termination) | HTTPS/1.1 | h2load (0b–1m) |
| `amphora-http2` | HTTPS (reused) | HTTP/2 over TLS | h2load (0b–1m) |
| `amphora-https-reencryption` | HTTPS re-encryption | HTTPS re-encrypt | h2load (0b–1m, `/data/N` endpoints) |

> **Note:** The `amphora-udp` role exists in `setup-loadbalancers.yaml` for creating a UDP load balancer, but no benchmark workload is defined for it in `run-workloads.yaml` yet.

#### LB Reuse

Some workloads share the same load balancer to avoid unnecessary recreation:

- `amphora-http` and `amphora-http2-clear` share the **HTTP LB**
- `amphora-https` and `amphora-http2` share the **HTTPS LB**

When running a dependent tag alone (e.g., `--tags amphora-http2-clear`), the LB is automatically created first.

#### Test Matrix

Most workloads run 8 tests with varying response body sizes (`amphora-http-503` runs only the 0b tests):

| Test | Body Size | Connections | Duration |
|---|---|---|---|
| 0b-keepalive | 0 bytes | 750 | 120s |
| 0b-nokeepalive | 0 bytes | 750 | 100k requests |
| 128b | 128 bytes | 750 | 120s |
| 1k | 1 KB | 750 | 120s |
| 10k | 10 KB | 750 | 120s |
| 128k | 128 KB | 750 | 120s |
| 512k | 512 KB | 750 | 120s |
| 1m | 1 MB | 150 | 120s |

All tests use 15 threads and a 30-second warm-up period.

### Step 4: View Results

When workloads complete, results are saved to `results/<workload_name>-<timestamp>/` and include:

- `summary.txt` / `summary.csv` — benchmark results (req/s, bandwidth)
- `metrics/` — per-test, per-run CSV files for compute and amphora metrics
- `report.html` — self-contained HTML report with all data (generated automatically when `collect_metrics=true`)

#### Re-generate the HTML report

```bash
python3 generate-report.py results/amphora-http-20260521-023000/ -o report.html
```

#### Parse results manually

Use `parse-results.py` to parse benchmark logs and generate a summary. By default it prints formatted tables to the terminal:

```bash
python3 parse-results.py results/amphora-http-20260521-023000
```

Save results to files (generates both `summary.txt` table and `summary.csv`):

```bash
python3 parse-results.py results/amphora-http-20260521-023000 -o summary
```

Use `--format csv` to print CSV to stdout instead of tables:

```bash
python3 parse-results.py results/amphora-http-20260521-023000 --format csv
```

#### Output Format

**Table output** (default on screen, and saved to `.txt` with `-o`):

```
  Workload: amphora-http-20260521-023000  |  Runs: 3

  Detailed Results
┌──────────────────┬────────┬────────────┬────────────┬────────────┬──────────┬──────────┬──────────┬──────────┐
│ Test             │ Metric │        Avg │        Min │        Max │  Std Dev │    Run 1 │    Run 2 │    Run 3 │
├──────────────────┼────────┼────────────┼────────────┼────────────┼──────────┼──────────┼──────────┼──────────┤
│ http-0b-keepalive│  req/s │ 145,798.06 │ 129,554.44 │ 170,001.52 │21,366.11│ ...      │ ...      │ ...      │
│ http-128b        │  Mbps  │     312.19 │     262.08 │     347.28 │    44.54│ ...      │ ...      │ ...      │
└──────────────────┴────────┴────────────┴────────────┴────────────┴──────────┴──────────┴──────────┴──────────┘

  Per-test Summary (req/s)
┌──────────────────┬────────────┬────────────┬────────────┬──────────┐
│ Test             │        Avg │        Min │        Max │  Std Dev │
├──────────────────┼────────────┼────────────┼────────────┼──────────┤
│ ...              │            │            │            │          │
└──────────────────┴────────────┴────────────┴────────────┴──────────┘

  Per-test Summary (Bandwidth Mbps)
┌──────────────────┬────────────┬────────────┬────────────┬──────────┐
│ Test             │        Avg │        Min │        Max │  Std Dev │
├──────────────────┼────────────┼────────────┼────────────┼──────────┤
│ ...              │            │            │            │          │
└──────────────────┴────────────┴────────────┴────────────┴──────────┘
```

**CSV output** (saved to `.csv` with `-o`, or printed with `--format csv`):

```
test,metric,avg,min,max,std_dev,run1,run2,run3
http-0b-keepalive,req/s,145798.06,129554.44,170001.52,21366.11,...
```

### Step 5: Cleanup

Tears down all resources (VMs, networks, LBs, flavors, AZs, etc.):

```bash
ansible-playbook cleanup-infrastructure.yaml
```

## Configuration

Key variables in `group_vars/all.yml`:

| Variable | Default | Description |
|---|---|---|
| `lb_name` | `lb-amphora` | Load balancer name |
| `lb_flavor` | `standalone-4vcpus-mq` | Octavia LB flavor |
| `lb_az` | `octavia` | Octavia availability zone |
| `connection_limit` | `1000000` | Listener connection limit |
| `member_names` | naruto-member1, sasuke-member2, sakura-member3 | Backend member VM names |
| `metrics_interval` | `1` | Metrics sampling interval in seconds |
| `hypervisor_ssh_key_secret` | `dataplane-ansible-ssh-private-key-secret` | OpenShift secret containing hypervisor SSH key |
| `hypervisor_user` | `cloud-admin` | SSH user on the hypervisor |
| `prometheus_listener_port` | `8088` | Amphora Prometheus metrics port |

Workload settings in `roles/run-workload/defaults/main.yml`:

| Variable | Default | Description |
|---|---|---|
| `os_cloud` | `default` | OpenStack cloud name from `clouds.yaml` |
| `h2load_bin` | `~/nghttp2/src/h2load` | Path to h2load binary on client VM |
| `wrk_bin` | `~/wrk/wrk` | Path to wrk binary on client VM |
| `results_base_dir` | `~/results` | Base directory for results on client VM |
| `num_runs` | `1` | Number of times to repeat each test |
| `collect_metrics` | `false` | Enable hypervisor + amphora metrics collection |
| `delay_between_tests` | `300` | Seconds to wait between tests |

Flavor settings in `roles/create-flavor/defaults/main.yml`:

| Variable | Default | Description |
|---|---|---|
| `amphora_disk` | `10` | Amphora VM disk size (GB) |
| `amphora_vcpu_counts` | 2, 4, 8, 16, 32, 64 | vCPU variants to create |
