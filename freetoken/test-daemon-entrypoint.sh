#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT HUP INT TERM
mkdir -p "$test_dir/bin"

cat > "$test_dir/bin/nvidia-smi" <<'MOCK'
#!/bin/sh
printf '%s\n' "$TEST_GPU_UUID"
MOCK

cat > "$test_dir/bin/ft" <<'MOCK'
#!/bin/sh
if [ "$1" = bench ]; then
    printf 'bench\n' >> "$TEST_CALLS"
    [ "${TEST_BENCH_FAIL:-0}" != 1 ] || exit 1
    if [ "$TEST_PROFILE_LAYOUT" = legacy ]; then
        profile="$XDG_CACHE_HOME/freetoken/benchbw.json"
    else
        profile="$XDG_CACHE_HOME/freetoken/benchbw/$TEST_GPU_UUID.json"
    fi
    mkdir -p "$(dirname "$profile")"
    printf '{"gpu":{"uuid":"%s"}}\n' "$TEST_GPU_UUID" > "$profile"
else
    printf '%s\n' "$1" >> "$TEST_CALLS"
fi
MOCK
chmod +x "$test_dir/bin/nvidia-smi" "$test_dir/bin/ft"

export PATH="$test_dir/bin:$PATH"
export XDG_CACHE_HOME="$test_dir/cache"
export TEST_CALLS="$test_dir/calls"
export TEST_GPU_UUID=GPU-first
export TEST_PROFILE_LAYOUT=per-gpu

run_daemon() {
    sh "$repo_dir/daemon-entrypoint.sh" ft daemon > "$test_dir/output" 2>&1
}

run_serve() {
    sh "$repo_dir/daemon-entrypoint.sh" ft serve > "$test_dir/output" 2>&1
}

run_other() {
    sh "$repo_dir/daemon-entrypoint.sh" ft --version > "$test_dir/output" 2>&1
}

bench_count() {
    awk '$0 == "bench" { n++ } END { print n+0 }' "$TEST_CALLS"
}

run_other
[ "$(bench_count)" -eq 0 ]
[ ! -e "$XDG_CACHE_HOME/freetoken" ]

run_serve
[ "$(bench_count)" -eq 1 ]
[ -f "$XDG_CACHE_HOME/freetoken/autobench/GPU-first.done" ]

run_daemon
[ "$(bench_count)" -eq 1 ]
run_daemon
[ "$(bench_count)" -eq 1 ]

rm "$XDG_CACHE_HOME/freetoken/benchbw/GPU-first.json"
run_daemon
[ "$(bench_count)" -eq 2 ]

export TEST_GPU_UUID=GPU-second
run_daemon
[ "$(bench_count)" -eq 3 ]

rm -rf "$XDG_CACHE_HOME"
: > "$TEST_CALLS"
export TEST_PROFILE_LAYOUT=legacy
run_daemon
run_daemon
[ "$(bench_count)" -eq 1 ]

rm -rf "$XDG_CACHE_HOME"
: > "$TEST_CALLS"
export TEST_BENCH_FAIL=1
run_daemon
[ ! -f "$XDG_CACHE_HOME/freetoken/autobench/GPU-second.done" ]
unset TEST_BENCH_FAIL
run_daemon
[ "$(bench_count)" -eq 2 ]
[ -f "$XDG_CACHE_HOME/freetoken/autobench/GPU-second.done" ]

echo 'daemon entrypoint: OK'
