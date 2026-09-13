#!/bin/sh
set -eu

# Calibrate before either server command; leave other ft commands untouched.
case "${1:-} ${2:-}" in
    "ft daemon"|"ft serve") ;;
    *) exec "$@" ;;
esac

# FreeToken reads this cache when resolving the MoE auto/hybrid strategy.
cache_dir="${XDG_CACHE_HOME:-/root/.cache}/freetoken"
gpu_uuid="$(nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | head -n 1 | tr -d '\r')"

if [ -z "$gpu_uuid" ]; then
    echo "FreeToken auto bench: GPU UUID unavailable; skipping calibration" >&2
else
    marker="$cache_dir/autobench/$gpu_uuid.done"
    # Older FreeToken versions write benchbw.json; newer ones write benchbw/<UUID>.json.
    if [ -f "$marker" ] && { [ -s "$cache_dir/benchbw/$gpu_uuid.json" ] || [ -s "$cache_dir/benchbw.json" ]; }; then
        echo "FreeToken auto bench: using cached profile for $gpu_uuid"
    else
        echo "FreeToken auto bench: calibrating GPU $gpu_uuid"
        if ft bench bw && { [ -s "$cache_dir/benchbw/$gpu_uuid.json" ] || [ -s "$cache_dir/benchbw.json" ]; }; then
            mkdir -p "$cache_dir/autobench"
            : > "$marker"
            echo "FreeToken auto bench: profile saved for $gpu_uuid"
        else
            echo "FreeToken auto bench: calibration failed; will retry on next start" >&2
        fi
    fi
fi

exec "$@"
