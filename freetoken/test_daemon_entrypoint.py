"""GPU-free tests for the Python image entrypoint."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import daemon_entrypoint as entrypoint


class EntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "freetoken"
        self.events: list[str] = []
        self.stdout = StringIO()
        self.stderr = StringIO()

    def profile(self, uuid: str = "GPU-first", *, legacy: bool = False) -> Path:
        path = self.cache / ("benchbw.json" if legacy else f"benchbw/{uuid}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"gpu":{"name":"Test GPU","uuid":"' + uuid + '"},'
            '"dtypes":{"nvfp4":"hybrid","bf16":"offload"},'
            '"dtype_kernels":{"nvfp4":{"cpu_moe_gbs":30,'
            '"pcie_gather_gbs":10,"ratio":3}}}',
            encoding="utf-8",
        )
        return path

    def run_entrypoint(self, command: list[str], runner) -> None:
        with (
            patch.dict(os.environ, {"XDG_CACHE_HOME": self.temp.name}),
            patch.object(entrypoint.subprocess, "run", side_effect=runner),
            patch.object(entrypoint.os, "execvp", side_effect=lambda *_: self.events.append("exec")),
            redirect_stdout(self.stdout),
            redirect_stderr(self.stderr),
        ):
            entrypoint.main(command)

    def test_serve_waits_for_benchmark_and_prints_result(self) -> None:
        def runner(args, **_kwargs):
            if args[0] == "nvidia-smi":
                self.events.append("gpu")
                return subprocess.CompletedProcess(args, 0, "GPU-first\n", "")
            self.events.append("bench-start")
            self.profile()
            self.events.append("bench-finished")
            return subprocess.CompletedProcess(args, 0)

        self.run_entrypoint(["ft", "serve"], runner)
        self.assertEqual(self.events, ["gpu", "bench-start", "bench-finished", "exec"])
        self.assertTrue((self.cache / "autobench/GPU-first.done").is_file())
        self.assertIn("nvfp4: hybrid (CPU 30 GB/s, PCIe 10 GB/s, 3x)", self.stdout.getvalue())
        self.assertLess(self.stdout.getvalue().index("results for"), self.stdout.getvalue().index("starting ft serve"))

    def test_daemon_reuses_profile(self) -> None:
        self.profile()
        marker = self.cache / "autobench/GPU-first.done"
        marker.parent.mkdir(parents=True)
        marker.touch()

        def runner(args, **_kwargs):
            self.assertEqual(args[0], "nvidia-smi")
            self.events.append("gpu")
            return subprocess.CompletedProcess(args, 0, "GPU-first\n", "")

        self.run_entrypoint(["ft", "daemon"], runner)
        self.assertEqual(self.events, ["gpu", "exec"])
        self.assertIn("using cached profile", self.stdout.getvalue())
        self.assertIn("bf16: offload", self.stdout.getvalue())

    def test_legacy_profile_is_accepted(self) -> None:
        def runner(args, **_kwargs):
            if args[0] == "nvidia-smi":
                return subprocess.CompletedProcess(args, 0, "GPU-first\n", "")
            self.profile(legacy=True)
            return subprocess.CompletedProcess(args, 0)

        self.run_entrypoint(["ft", "daemon"], runner)
        self.assertTrue((self.cache / "autobench/GPU-first.done").is_file())
        self.assertIn("nvfp4: hybrid", self.stdout.getvalue())

    def test_failure_starts_server_without_marker(self) -> None:
        def runner(args, **_kwargs):
            if args[0] == "nvidia-smi":
                return subprocess.CompletedProcess(args, 0, "GPU-first\n", "")
            return subprocess.CompletedProcess(args, 1)

        self.run_entrypoint(["ft", "daemon"], runner)
        self.assertFalse((self.cache / "autobench/GPU-first.done").exists())
        self.assertIn("calibration failed", self.stderr.getvalue())
        self.assertEqual(self.events, ["exec"])

    def test_other_command_does_not_benchmark(self) -> None:
        self.run_entrypoint(["ft", "--version"], lambda *_args, **_kwargs: self.fail("unexpected command"))
        self.assertEqual(self.events, ["exec"])


if __name__ == "__main__":
    unittest.main()
