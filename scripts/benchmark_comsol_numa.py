"""Non-destructive Dual-Xeon / NUMA scaling benchmark for COMSOL 6.4 3D C04 CFD.

Runs COMSOL 6.4 batch workloads in strictly ``-nosave`` mode against the 1.55M-element
``c04_re126k_s0_mesh2.mph`` model (verifying SHA-256 immutability before and after),
isolating all COMSOL temp/OSGi/recovery files on E:\\comsol_scratch.

Supported profiles test single-job strong scaling vs multi-socket throughput:
- np16_numa1        : -np 16 -numasets 1 -numafirst 0   (1 NUMA socket, physical cores)
- np32_numa1        : -np 32 -numasets 1 -numafirst 0   (1 NUMA socket, SMT 32 threads)
- np32_numa2        : -np 32 -numasets 2                (2 NUMA sockets, 32 physical cores)
- np64_numa2        : -np 64 -numasets 2                (2 NUMA sockets, all 64 threads)
- dual_socket_2x16  : 2 concurrent workers (-np 16 -numasets 1 -numafirst 0 & 1)
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.comsol_java.bridge import find_comsol_root, get_comsol_bin_dir

JAVA_SOURCE = r"""import com.comsol.model.*;
import com.comsol.model.util.*;
import java.util.Locale;

public class BenchC04Numa {
    private static String extractLineValue(String msg, String... prefixes) {
        if (msg == null) return "";
        String best = "";
        for (String line : msg.split("\\R")) {
            String tr = line.trim();
            for (String p : prefixes) {
                if (tr.startsWith(p)) {
                    best = tr;
                }
            }
        }
        return best;
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.out.println("Usage: BenchC04Numa <model_path> <workload: wdi|transient_step> [t_step]");
            return;
        }
        String modelPath = args[0];
        String workload = args[1];
        String tStep = args.length >= 3 ? args[2] : "0.0005";

        long t0 = System.nanoTime();
        Model model = ModelUtil.loadCopy("BenchModel", modelPath);
        long t1 = System.nanoTime();

        SolverSequence sol = model.sol("sol3");
        double[] pvals = null;
        long s0 = 0L;
        long s1 = 0L;

        if ("wdi".equalsIgnoreCase(workload)) {
            s0 = System.nanoTime();
            sol.run("s1");
            s1 = System.nanoTime();
            pvals = sol.getPVals();
        } else if ("transient_step".equalsIgnoreCase(workload)) {
            try {
                StudyFeature stTime = model.study("std2").feature("time");
                stTime.set("tlist", "range(0, " + tStep + ", " + tStep + ")");
                stTime.set("usertol", "on");
                stTime.set("rtol", "0.005");
            } catch (Throwable ignored) {}
            SolverFeature timeFeat = sol.feature("t1");
            timeFeat.set("tlist", "range(0, " + tStep + ", " + tStep + ")");
            timeFeat.set("rtol", "0.005");
            timeFeat.set("initialstepbdfactive", true);
            timeFeat.set("initialstepbdf", tStep);
            timeFeat.set("maxstepconstraintbdf", "const");
            timeFeat.set("maxstepbdf", tStep);

            s0 = System.nanoTime();
            model.study("std2").runNoGen();
            s1 = System.nanoTime();
            sol = model.sol("sol3");
            pvals = sol.getPVals();
        } else {
            throw new IllegalArgumentException("Unknown workload: " + workload);
        }

        String s1Msg = "";
        String t1Msg = "";
        try { s1Msg = sol.feature("s1").getString("message"); } catch (Throwable ignored) {}
        try { t1Msg = sol.feature("t1").getString("message"); } catch (Throwable ignored) {}

        System.out.printf(
            Locale.US,
            "BENCH_METRICS load_s=%.3f solve_s=%.3f pvals_len=%d end_t=%.6f s1_time=%s t1_time=%s%n",
            (t1 - t0) / 1e9,
            (s1 - s0) / 1e9,
            pvals == null ? 0 : pvals.length,
            (pvals != null && pvals.length > 0) ? pvals[pvals.length - 1] : -1.0,
            extractLineValue(s1Msg, "Solution time:", "求解时间：", "时间：").replace(' ', '_'),
            extractLineValue(t1Msg, "Solution time:", "求解时间：", "时间：").replace(' ', '_')
        );
    }
}
"""


DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "np16_numa1": {
        "description": "1 NUMA socket, 16 physical cores (-mpmode turnaround)",
        "workers": [{"np": 16, "numasets": 1, "numafirst": 0, "mpmode": "turnaround"}],
    },
    "np32_numa1": {
        "description": "1 NUMA socket, 32 logical SMT threads (-mpmode turnaround)",
        "workers": [{"np": 32, "numasets": 1, "numafirst": 0, "mpmode": "turnaround"}],
    },
    "np32_numa2": {
        "description": "2 NUMA sockets, 32 physical cores (-mpmode turnaround)",
        "workers": [{"np": 32, "numasets": 2, "numafirst": 0, "mpmode": "turnaround"}],
    },
    "np64_numa2": {
        "description": "2 NUMA sockets, all 64 logical threads (-mpmode turnaround)",
        "workers": [{"np": 64, "numasets": 2, "numafirst": 0, "mpmode": "turnaround"}],
    },
    "np64_numa2_throughput": {
        "description": "2 NUMA sockets, all 64 logical threads (-mpmode throughput)",
        "workers": [{"np": 64, "numasets": 2, "numafirst": 0, "mpmode": "throughput"}],
    },
    "dual_socket_2x16": {
        "description": "2 parallel workers pinned to Socket 0 (-np 16) & Socket 1 (-np 16), turnaround",
        "workers": [
            {"np": 16, "numasets": 1, "numafirst": 0, "mpmode": "turnaround"},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": "turnaround"},
        ],
    },
    "dual_socket_2x16_throughput": {
        "description": "2 parallel workers pinned to Socket 0 (-np 16) & Socket 1 (-np 16), throughput",
        "workers": [
            {"np": 16, "numasets": 1, "numafirst": 0, "mpmode": "throughput"},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
        ],
    },
    "dual_socket_2x24_throughput": {
        "description": "2 parallel workers pinned to Socket 0 (-np 24) & Socket 1 (-np 24), throughput (48T)",
        "workers": [
            {"np": 24, "numasets": 1, "numafirst": 0, "mpmode": "throughput"},
            {"np": 24, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
        ],
    },
    "dual_socket_2x32_turnaround": {
        "description": "2 parallel workers pinned to Socket 0 (-np 32) & Socket 1 (-np 32), turnaround (64T)",
        "workers": [
            {"np": 32, "numasets": 1, "numafirst": 0, "mpmode": "turnaround"},
            {"np": 32, "numasets": 1, "numafirst": 1, "mpmode": "turnaround"},
        ],
    },
    "dual_socket_2x32_throughput": {
        "description": "2 parallel workers pinned to Socket 0 (-np 32) & Socket 1 (-np 32), throughput (64T)",
        "workers": [
            {"np": 32, "numasets": 1, "numafirst": 0, "mpmode": "throughput"},
            {"np": 32, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
        ],
    },
    "triple_1x32_2x16_throughput": {
        "description": "3 parallel workers (Node0 -np 32 + 2x Node1 -np 16, throughput, 64T total)",
        "workers": [
            {"np": 32, "numasets": 1, "numafirst": 0, "mpmode": "throughput"},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
        ],
    },
    "triple_1x16_2x16_throughput": {
        "description": "3 parallel workers (Node0 -np 16 + 2x Node1 -np 16, throughput, 48T total)",
        "workers": [
            {"np": 16, "numasets": 1, "numafirst": 0, "mpmode": "throughput"},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
        ],
    },
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest().upper()


def ensure_probe_compiled(comsol_root: Path, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    java_file = work_dir / "BenchC04Numa.java"
    class_file = work_dir / "BenchC04Numa.class"
    if not java_file.is_file() or java_file.read_text(encoding="utf-8") != JAVA_SOURCE:
        java_file.write_text(JAVA_SOURCE, encoding="utf-8")
        if class_file.is_file():
            class_file.unlink()

    if not class_file.is_file() or class_file.stat().st_mtime < java_file.stat().st_mtime:
        comsolcompile = get_comsol_bin_dir(comsol_root) / "comsolcompile.exe"
        proc = subprocess.run(
            [str(comsolcompile), str(java_file)],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0 or not class_file.is_file():
            javac = comsol_root / "java" / "win64" / "jre" / "bin" / "javac.exe"
            proc2 = subprocess.run(
                [
                    str(javac),
                    "-encoding",
                    "UTF-8",
                    "-proc:none",
                    "-cp",
                    str(comsol_root / "plugins" / "*"),
                    str(java_file),
                ],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if proc2.returncode != 0 or not class_file.is_file():
                raise RuntimeError(
                    f"Failed compiling BenchC04Numa.java:\n{proc.stdout}\n{proc2.stderr}"
                )
    return class_file


def parse_comsol_log(stdout: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    m = re.search(
        r"BENCH_METRICS load_s=([0-9.]+) solve_s=([0-9.]+) pvals_len=(\d+) end_t=([0-9eE.+-]+) s1_time=(\S*) t1_time=(\S*)",
        stdout,
    )
    if m:
        out["load_s"] = float(m.group(1))
        out["solve_s"] = float(m.group(2))
        out["pvals_len"] = int(m.group(3))
        out["end_t"] = float(m.group(4))
        out["s1_time_msg"] = m.group(5)
        out["t1_time_msg"] = m.group(6)

    # Extract COMSOL-reported Stationary / Time-Dependent seconds & peak memory
    sol_times = [float(x) for x in re.findall(r"(?:求解时间|Solution time)[：:]\s*([0-9.]+)\s*s", stdout)]
    phys_mems = [float(x) for x in re.findall(r"(?:物理内存|Physical memory)[：:]\s*([0-9.]+)\s*GB", stdout)]
    virt_mems = [float(x) for x in re.findall(r"(?:虚拟内存|Virtual memory)[：:]\s*([0-9.]+)\s*GB", stdout)]
    out["comsol_stage_times_s"] = sol_times
    out["peak_phys_gb"] = max(phys_mems) if phys_mems else None
    out["peak_virt_gb"] = max(virt_mems) if virt_mems else None
    return out


def _priority_creation_flags(priority: str) -> int:
    if sys.platform != "win32":
        return 0
    key = (priority or "normal").lower()
    if key == "abovenormal":
        return int(getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0x00008000))
    if key == "high":
        return int(getattr(subprocess, "HIGH_PRIORITY_CLASS", 0x00000080))
    return 0


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class SystemTelemetrySampler:
    """Lightweight Windows CPU% + Free RAM sampler using Win32 kernel32 API."""

    def __init__(self, interval_s: float = 1.5) -> None:
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.cpu_samples: list[float] = []
        self.used_ram_gb_samples: list[float] = []
        self.total_ram_gb: float | None = None

    @staticmethod
    def _read_system_times() -> tuple[int, int, int] | None:
        if sys.platform != "win32":
            return None
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        to_int = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return to_int(idle), to_int(kernel), to_int(user)

    @staticmethod
    def _read_memory_gb() -> tuple[float, float] | None:
        if sys.platform != "win32":
            return None
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        total_gb = st.ullTotalPhys / (1024.0 ** 3)
        used_gb = (st.ullTotalPhys - st.ullAvailPhys) / (1024.0 ** 3)
        return total_gb, used_gb

    def _run(self) -> None:
        prev = self._read_system_times()
        while not self._stop.wait(self.interval_s):
            cur = self._read_system_times()
            if prev is not None and cur is not None:
                di = cur[0] - prev[0]
                dk = cur[1] - prev[1]
                du = cur[2] - prev[2]
                total = dk + du
                if total > 0:
                    busy = (dk - di) + du
                    self.cpu_samples.append(max(0.0, min(100.0, 100.0 * busy / total)))
            prev = cur
            mem = self._read_memory_gb()
            if mem is not None:
                self.total_ram_gb = mem[0]
                self.used_ram_gb_samples.append(mem[1])

    def start(self) -> None:
        if sys.platform != "win32":
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop_and_summary(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if not self.cpu_samples:
            return {}
        return {
            "samples": len(self.cpu_samples),
            "avg_cpu_pct": round(sum(self.cpu_samples) / len(self.cpu_samples), 1),
            "p90_cpu_pct": round(sorted(self.cpu_samples)[int(0.9 * (len(self.cpu_samples) - 1))], 1),
            "max_cpu_pct": round(max(self.cpu_samples), 1),
            "peak_sys_used_ram_gb": round(max(self.used_ram_gb_samples), 2) if self.used_ram_gb_samples else None,
            "total_sys_ram_gb": round(self.total_ram_gb, 2) if self.total_ram_gb else None,
        }


def run_single_worker(
    *,
    comsolbatch: Path,
    class_file: Path,
    model_path: Path,
    ini_file: Path,
    scratch_root: Path,
    workload: str,
    t_step: str,
    worker_cfg: dict[str, Any],
    worker_idx: int,
    log_dir: Path,
    tag: str,
    alloc: str = "scalable",
    priority: str = "abovenormal",
) -> dict[str, Any]:
    np_val = int(worker_cfg["np"])
    numasets = int(worker_cfg["numasets"])
    numafirst = int(worker_cfg["numafirst"])
    mpmode = str(worker_cfg.get("mpmode", "turnaround"))

    w_tmp = scratch_root / "tmp" / f"bench_{tag}_w{worker_idx}"
    w_rec = scratch_root / "recoveries" / f"bench_{tag}_w{worker_idx}"
    w_tmp.mkdir(parents=True, exist_ok=True)
    w_rec.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TEMP"] = str(w_tmp)
    env["TMP"] = str(w_tmp)

    cmd = [
        str(comsolbatch),
        "-nosave",
        "-comsolinifile",
        str(ini_file),
        "-tmpdir",
        str(w_tmp),
        "-recoverydir",
        str(w_rec),
        "-autosave",
        "off",
        "-np",
        str(np_val),
        "-numasets",
        str(numasets),
        "-numafirst",
        str(numafirst),
        "-mpmode",
        mpmode,
        "-alloc",
        alloc,
        "-blas",
        "mkl",
        "-inputfile",
        str(class_file),
        str(model_path),
        workload,
        t_step,
    ]

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{tag}_w{worker_idx}.log"
    console_enc = "mbcs" if sys.platform == "win32" else "utf-8"

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    t_start = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(class_file.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=console_enc,
        errors="replace",
        bufsize=1,
        creationflags=_priority_creation_flags(priority),
    )

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)

    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_err.start()
    with log_file.open("w", encoding="utf-8") as lf:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_chunks.append(line)
            lf.write(line)
            lf.flush()
    t_err.join(timeout=5.0)
    rc = proc.wait()
    wall_s = time.perf_counter() - t_start

    stderr_text = "".join(stderr_chunks)
    if stderr_text:
        with log_file.open("a", encoding="utf-8") as lf:
            lf.write("\n--- STDERR ---\n" + stderr_text)

    stdout_text = "".join(stdout_chunks)
    parsed = parse_comsol_log(stdout_text)
    status_file = class_file.with_name(f"{class_file.name}.status")
    if status_file.is_file():
        status_file.unlink(missing_ok=True)

    return {
        "worker_idx": worker_idx,
        "np": np_val,
        "numasets": numasets,
        "numafirst": numafirst,
        "mpmode": mpmode,
        "alloc": alloc,
        "priority": priority,
        "returncode": rc,
        "wall_s": round(wall_s, 3),
        "log_file": str(log_file),
        **parsed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Dual-Xeon NUMA scaling for 3D COMSOL C04")
    parser.add_argument(
        "--model",
        default=r"E:\cylinder_3d\c04_re126k_s0_mesh2.mph",
        help="Input unsolved mesh2 C04 model (strictly read-only)",
    )
    parser.add_argument(
        "--ini",
        default=r"E:\comsol_scratch\comsolbatch_perf.ini",
        help="Path to comsolbatch INI tuned for E:\\comsol_scratch",
    )
    parser.add_argument(
        "--scratch",
        default=r"E:\comsol_scratch",
        help="Scratch directory on E:\\",
    )
    parser.add_argument(
        "--workload",
        choices=["wdi", "transient_step"],
        default="transient_step",
        help="wdi (~30s 273k-DOF Stationary WDI) or transient_step (WDI + Step 0+1 1.64M-DOF BDF)",
    )
    parser.add_argument(
        "--t-step",
        default="0.0005",
        help="BDF initial & output step size for transient_step workload (default: 0.0005)",
    )
    parser.add_argument(
        "--profiles",
        default="np16_numa1,np32_numa2,np64_numa2,dual_socket_2x16",
        help="Comma-separated profiles from: " + ",".join(DEFAULT_PROFILES.keys()),
    )
    parser.add_argument(
        "--alloc",
        choices=["auto", "native", "scalable"],
        default="scalable",
        help="COMSOL memory allocator (default: scalable)",
    )
    parser.add_argument(
        "--priority",
        choices=["normal", "abovenormal", "high"],
        default="abovenormal",
        help="Windows process scheduling priority for comsolbatch workers (default: abovenormal)",
    )
    parser.add_argument(
        "--report-name",
        default=None,
        help="Optional custom report base filename (default: numa_benchmark_<workload>)",
    )
    parser.add_argument(
        "--out-dir",
        default=r"E:\cfd_3d\05_reports_viz",
        help="Report output directory",
    )
    args = parser.parse_args(argv)

    model_path = Path(args.model).resolve()
    if not model_path.is_file():
        raise FileNotFoundError(f"Model not found: {model_path}")
    ini_file = Path(args.ini).resolve()
    if not ini_file.is_file():
        raise FileNotFoundError(f"INI file not found: {ini_file}")

    scratch_root = Path(args.scratch).resolve()
    work_dir = scratch_root / "bench_tmp"
    log_dir = scratch_root / "logs" / f"numa_bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    comsol_root = find_comsol_root()
    comsolbatch = get_comsol_bin_dir(comsol_root) / "comsolbatch.exe"
    class_file = ensure_probe_compiled(comsol_root, work_dir)

    selected = [p.strip() for p in args.profiles.split(",") if p.strip()]
    for name in selected:
        if name not in DEFAULT_PROFILES:
            raise ValueError(f"Unknown profile '{name}'. Choices: {list(DEFAULT_PROFILES)}")

    hash_before = sha256_file(model_path)
    print(f"[INFO] Source model: {model_path} (SHA256: {hash_before[:16]}...)")
    print(
        f"[INFO] Workload: {args.workload} (t_step={args.t_step}), alloc={args.alloc}, "
        f"priority={args.priority}, Profiles: {selected}"
    )

    results: list[dict[str, Any]] = []
    for name in selected:
        spec = DEFAULT_PROFILES[name]
        workers_cfg = spec["workers"]
        print(f"\n[RUN] {name} — {spec['description']}", flush=True)
        sampler = SystemTelemetrySampler(interval_s=1.5)
        sampler.start()
        t_prof0 = time.perf_counter()
        if len(workers_cfg) == 1:
            worker_res = [
                run_single_worker(
                    comsolbatch=comsolbatch,
                    class_file=class_file,
                    model_path=model_path,
                    ini_file=ini_file,
                    scratch_root=scratch_root,
                    workload=args.workload,
                    t_step=args.t_step,
                    worker_cfg=workers_cfg[0],
                    worker_idx=0,
                    log_dir=log_dir,
                    tag=name,
                    alloc=args.alloc,
                    priority=args.priority,
                )
            ]
        else:
            with ThreadPoolExecutor(max_workers=len(workers_cfg)) as pool:
                futs = [
                    pool.submit(
                        run_single_worker,
                        comsolbatch=comsolbatch,
                        class_file=class_file,
                        model_path=model_path,
                        ini_file=ini_file,
                        scratch_root=scratch_root,
                        workload=args.workload,
                        t_step=args.t_step,
                        worker_cfg=cfg,
                        worker_idx=idx,
                        log_dir=log_dir,
                        tag=name,
                        alloc=args.alloc,
                        priority=args.priority,
                    )
                    for idx, cfg in enumerate(workers_cfg)
                ]
                worker_res = [f.result() for f in futs]
        wall_prof = time.perf_counter() - t_prof0
        sys_telemetry = sampler.stop_and_summary()

        ok = all(w["returncode"] == 0 and "solve_s" in w for w in worker_res)
        max_solve_s = max((w.get("solve_s", wall_prof) for w in worker_res), default=wall_prof)
        mean_solve_s = sum((w.get("solve_s", wall_prof) for w in worker_res)) / max(1, len(worker_res))
        throughput_jobs_per_hr = (len(workers_cfg) * 3600.0 / wall_prof) if ok and wall_prof > 0 else 0.0

        row = {
            "profile": name,
            "description": spec["description"],
            "n_workers": len(workers_cfg),
            "ok": ok,
            "wall_s": round(wall_prof, 3),
            "mean_solve_s": round(mean_solve_s, 3),
            "max_solve_s": round(max_solve_s, 3),
            "throughput_jobs_per_hr": round(throughput_jobs_per_hr, 2),
            "system_telemetry": sys_telemetry,
            "workers": worker_res,
        }
        results.append(row)
        print(
            f"[DONE] {name}: ok={ok}, wall={wall_prof:.2f}s, mean_solve={mean_solve_s:.2f}s, "
            f"throughput={throughput_jobs_per_hr:.2f} jobs/hr, cpu_avg={sys_telemetry.get('avg_cpu_pct')}%",
            flush=True,
        )

    hash_after = sha256_file(model_path)
    if hash_before != hash_after:
        raise RuntimeError(f"Source model SHA256 changed unexpectedly! {hash_before} -> {hash_after}")

    # Baseline comparison vs first ok profile
    base = next((r for r in results if r["ok"]), None)
    if base:
        for r in results:
            if r["ok"] and r["wall_s"] > 0:
                r["single_job_speedup"] = round(base["mean_solve_s"] / r["mean_solve_s"], 3)
                r["throughput_speedup"] = round(
                    r["throughput_jobs_per_hr"] / base["throughput_jobs_per_hr"], 3
                )

    report = {
        "timestamp": datetime.now().isoformat(),
        "model": str(model_path),
        "model_sha256": hash_after,
        "workload": args.workload,
        "t_step": args.t_step,
        "alloc": args.alloc,
        "priority": args.priority,
        "ini_file": str(ini_file),
        "results": results,
    }
    base_name = args.report_name or f"numa_benchmark_{args.workload}"
    json_path = out_dir / f"{base_name}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        f"# COMSOL 6.4 Dual-Xeon (32C/64T, 2 NUMA) Scaling Benchmark (`{args.workload}`)",
        "",
        f"- Model: `{model_path}` (SHA256 verified immutable: `{hash_after[:16]}...`)",
        f"- Allocator / Priority: `{args.alloc}` / `{args.priority}`",
        f"- Date: {report['timestamp']}",
        "",
        "| Profile | Workers | Wall (s) | Mean Solve (s) | Single-Job Speedup | Aggregate Throughput (jobs/hr) | Avg/Max CPU % | Sys Peak RAM (GB) | Worker Peak Phys/Virt (GB) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        phys = max((w.get("peak_phys_gb") or 0.0) for w in r["workers"])
        virt = max((w.get("peak_virt_gb") or 0.0) for w in r["workers"])
        tel = r.get("system_telemetry") or {}
        cpu_str = f"{tel.get('avg_cpu_pct', '-')}/{tel.get('max_cpu_pct', '-')}"
        ram_str = f"{tel.get('peak_sys_used_ram_gb', '-')}"
        md_lines.append(
            f"| `{r['profile']}` | {r['n_workers']} | {r['wall_s']:.2f} | {r['mean_solve_s']:.2f} | "
            f"{r.get('single_job_speedup', 0):.2f}x | {r['throughput_jobs_per_hr']:.2f} ({r.get('throughput_speedup', 0):.2f}x) | "
            f"{cpu_str} | {ram_str} | {phys:.2f} / {virt:.2f} |"
        )
    md_path = out_dir / f"{base_name}.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"\n[SAVED] {json_path}")
    print(f"[SAVED] {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
