#!/bin/bash
# Collects overall compute node (hypervisor) CPU and memory utilization.
# CPU is reported in ABSOLUTE terms: 100% = 1 CPU core fully busy.
# On a 64-core host, max total is 6400%.
#
# Usage: ./collect-compute-metrics.sh [interval_seconds]
# Example: ./collect-compute-metrics.sh 1
#
# Output CSV columns:
#   timestamp,num_cpus,cpu_user,cpu_system,cpu_softirq,cpu_irq,cpu_iowait,
#   cpu_idle,cpu_total,mem_total_mb,mem_used_mb,mem_available_mb,mem_used_pct

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

INTERVAL="${1:-1}"
NUM_CPUS=$(nproc 2>/dev/null) || NUM_CPUS=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null) || NUM_CPUS=1
CLK_TCK=$(getconf CLK_TCK 2>/dev/null) || CLK_TCK=100

for req in /proc/stat /proc/meminfo; do
    if [[ ! -r "$req" ]]; then
        echo "ERROR: Cannot read $req" >&2
        exit 1
    fi
done

get_time_ns() {
    local ns
    ns=$(date +%s%N 2>/dev/null)
    if [[ "$ns" =~ ^[0-9]+$ ]]; then
        echo "$ns"
    else
        echo "$(date +%s)000000000"
    fi
}

echo "# Compute node metrics: interval=${INTERVAL}s, num_cpus=${NUM_CPUS}, clk_tck=${CLK_TCK}" >&2
echo "# bash=$BASH_VERSION, date=$(date --version 2>&1 | head -1)" >&2
echo "timestamp,num_cpus,cpu_user,cpu_system,cpu_softirq,cpu_irq,cpu_iowait,cpu_idle,cpu_total,mem_total_mb,mem_used_mb,mem_available_mb,mem_used_pct"

read_cpu_ticks() {
    awk '/^cpu / {print $2, $3, $4, $5, $6, $7, $8, $9}' /proc/stat
}

read_memory() {
    awk '
    /^MemTotal:/     {total=$2}
    /^MemFree:/      {free=$2}
    /^MemAvailable:/ {available=$2}
    /^Buffers:/      {buffers=$2}
    /^Cached:/       {cached=$2}
    END {
        used = total - free - buffers - cached
        printf "%.2f,%.2f,%.2f,%.2f\n", \
            total/1024, used/1024, available/1024, (used*100/total)
    }
    ' /proc/meminfo
}

read prev_user prev_nice prev_system prev_idle prev_iowait prev_irq prev_softirq prev_steal <<< "$(read_cpu_ticks)"
PREV_NS=$(get_time_ns)

sleep "$INTERVAL"

while true; do
    read user nice system idle iowait irq softirq steal <<< "$(read_cpu_ticks)"
    CURR_NS=$(get_time_ns)

    TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    MEM=$(read_memory)

    awk_result=$(awk -v u=$((user - prev_user)) \
        -v n=$((nice - prev_nice)) \
        -v s=$((system - prev_system)) \
        -v idle_d=$((idle - prev_idle)) \
        -v iow=$((iowait - prev_iowait)) \
        -v hi=$((irq - prev_irq)) \
        -v si=$((softirq - prev_softirq)) \
        -v st=$((steal - prev_steal)) \
        -v delta_ns=$((CURR_NS - PREV_NS)) \
        -v clk_tck="$CLK_TCK" \
        'BEGIN {
            wall_ticks = delta_ns / 1000000000.0 * clk_tck
            if (wall_ticks > 0) {
                printf "%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f\n",
                    (u + n) / wall_ticks * 100,
                    s / wall_ticks * 100,
                    si / wall_ticks * 100,
                    hi / wall_ticks * 100,
                    iow / wall_ticks * 100,
                    idle_d / wall_ticks * 100,
                    (u + n + s + hi + si + iow + st) / wall_ticks * 100
            } else {
                printf "0.00,0.00,0.00,0.00,0.00,0.00,0.00\n"
            }
        }')

    echo "${TIMESTAMP},${NUM_CPUS},${awk_result},${MEM}"

    prev_user=$user; prev_nice=$nice; prev_system=$system; prev_idle=$idle
    prev_iowait=$iowait; prev_irq=$irq; prev_softirq=$softirq; prev_steal=$steal
    PREV_NS=$CURR_NS

    sleep "$INTERVAL"
done
