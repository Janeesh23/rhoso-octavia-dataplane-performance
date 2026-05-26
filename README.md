# RHOSO Octavia Dataplane Performance

Ansible automation for benchmarking Red Hat OpenStack Services on OpenShift (RHOSO) Octavia load balancer dataplane performance across multiple protocols and configurations.

## Prerequisites

- OpenStack CLI configured (`~/.config/openstack/clouds.yaml` with `default` cloud)
- `oc` access to the OpenShift cluster running RHOSO
- Ansible with required collections installed (see below)
- SSH access from the bastion host to the OpenStack tenant networks

Install dependencies:

```bash
ansible-galaxy collection install -r requirements.yml
```

## Project Structure

```
├── setup-infrastructure.yaml     # Full infrastructure setup
├── cleanup-infrastructure.yaml   # Full infrastructure teardown
├── setup-loadbalancers.yaml      # Create LB test scenarios only
├── run-workloads.yaml            # Run performance workloads
├── generate-inventory.yaml       # Generate static inventory file
├── parse-results.py              # Parse benchmark logs into table/CSV
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
    ├── amphora-tcp/              # Create TCP load balancer
    ├── amphora-https/            # Create HTTPS (TLS termination) load balancer
    ├── amphora-https-reencryption/ # Create HTTPS re-encryption load balancer
    ├── amphora-udp/              # Create UDP load balancer
    ├── run-workload/             # Generic workload runner (used by run-workloads.yaml)
    └── cleanup/                  # Tear down all resources
```

## Execution Flow

### Step 1: Setup Infrastructure

Creates networks, availability zones, flavors, VMs, and installs benchmarking tools:

```bash
ansible-playbook setup-infrastructure.yaml
```

This runs the following in order:

1. **create-network** — Tenant networks (vip, member, public), subnets, router
2. **create-az** — Host aggregates and Octavia availability zone
3. **prepare-image** — Upload CentOS 9 Stream image
4. **client** — Create client VM (kakashi-client1) with floating IP
5. **member** — Create member VMs (naruto, sasuke, sakura) with floating IPs
6. **cert-generation** — Generate TLS certificates for member and LB
7. **barbican-secret** — Store TLS secret in Barbican
8. **create-flavor** — Create Nova compute flavors and Octavia LB flavors
9. **build-tools** — Install h2load, wrk, nginx, iperf, httpterm on all VMs

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

### Step 4: Parse Results

Use `parse-results.py` to parse benchmark logs and generate a summary. By default it prints formatted tables to the terminal:

```bash
python3 parse-results.py ~/results/amphora-http-20260521-023000
```

Save results to files (generates both `summary.txt` table and `summary.csv`):

```bash
python3 parse-results.py ~/results/amphora-http-20260521-023000 -o summary
```

Use `--format csv` to print CSV to stdout instead of tables:

```bash
python3 parse-results.py ~/results/amphora-http-20260521-023000 --format csv
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
| `member_names` | naruto, sasuke, sakura | Backend member VM names |

Flavor settings in `roles/create-flavor/defaults/main.yml`:

| Variable | Default | Description |
|---|---|---|
| `amphora_disk` | `10` | Amphora VM disk size (GB) |
| `amphora_vcpu_counts` | 2, 4, 8, 16, 32, 64 | vCPU variants to create |
