"""Calibrate FreeToken once per GPU before starting ft daemon or ft serve."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def log(message: str, *, error: bool = False) -> None:
    print(f"FreeToken auto bench: {message}", file=sys.stderr if error else sys.stdout, flush=True)


def gpu_uuid() -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return next((line.strip() for line in result.stdout.splitlines() if line.strip()), None)


def load_profile(cache_dir: Path, uuid: str) -> tuple[Path, dict] | None:
    # FreeToken 0.1.2 writes benchbw.json; newer releases write one file per GPU.
    paths = (cache_dir / "benchbw" / f"{uuid}.json", cache_dir / "benchbw.json")
    for path in paths:
        try:
            with path.open(encoding="utf-8") as file:
                profile = json.load(file)
        except (OSError, ValueError):
            continue
        if not isinstance(profile, dict) or not isinstance(profile.get("dtypes"), dict):
            continue
        profile_uuid = (profile.get("gpu") or {}).get("uuid")
        if profile_uuid and profile_uuid != uuid:
            continue
        return path, profile
    return None


def print_result(path: Path, profile: dict) -> None:
    gpu_name = (profile.get("gpu") or {}).get("name") or "GPU"
    log(f"results for {gpu_name} ({path}):")
    recommendations = profile["dtypes"]
    if not recommendations:
        print("  No per-format recommendation in this profile", flush=True)
        return
    kernels = profile.get("dtype_kernels") or {}
    for fmt, backend in sorted(recommendations.items()):
        kernel = kernels.get(fmt) or {}
        cpu = kernel.get("cpu_moe_gbs")
        pcie = kernel.get("pcie_gather_gbs")
        ratio = kernel.get("ratio")
        if all(isinstance(value, (int, float)) for value in (cpu, pcie, ratio)):
            print(f"  {fmt}: {backend} (CPU {cpu:g} GB/s, PCIe {pcie:g} GB/s, {ratio:g}x)", flush=True)
        else:
            print(f"  {fmt}: {backend}", flush=True)


def calibrate() -> None:
    uuid = gpu_uuid()
    if not uuid:
        log("GPU UUID unavailable; skipping calibration", error=True)
        return

    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    cache_dir = cache_home / "freetoken"
    marker = cache_dir / "autobench" / f"{uuid}.done"
    cached = load_profile(cache_dir, uuid)
    if marker.is_file() and cached:
        log(f"using cached profile for {uuid}")
        print_result(*cached)
        return

    log(f"calibrating GPU {uuid}; waiting before server startup")
    try:
        result = subprocess.run(["ft", "bench", "bw"], check=False)
    except OSError as exc:
        log(f"calibration failed ({exc}); will retry on next start", error=True)
        return
    if result.returncode != 0:
        log(f"calibration failed (exit {result.returncode}); will retry on next start", error=True)
        return

    profile = load_profile(cache_dir, uuid)
    if not profile:
        log("no usable profile was saved; will retry on next start", error=True)
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as exc:
        log(f"cannot save completion marker ({exc}); will retry on next start", error=True)
    else:
        log(f"profile saved for {uuid}")
    print_result(*profile)


def main(command: list[str] | None = None) -> None:
    command = sys.argv[1:] if command is None else command
    if not command:
        raise SystemExit("No command to run")
    if command[:2] in (["ft", "daemon"], ["ft", "serve"]):
        calibrate()  # subprocess.run blocks until the benchmark finishes.
        log(f"starting {command[0]} {command[1]}")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
