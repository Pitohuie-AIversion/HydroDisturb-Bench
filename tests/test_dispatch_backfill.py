"""Unit tests for NUMA-aware COMSOL backfill queue dispatcher."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import dispatch_backfill as db


def _sample_queue(tmp_path: Path) -> Path:
    q = {
        "schema_version": 1,
        "policy": {"max_slots_per_numa_socket": 2},
        "running_now": {
            "N1-A": {"numafirst": 1, "pid": 111, "label": "old"},
        },
        "queue": [
            {
                "job_id": "Q1",
                "priority": 1,
                "status": "READY",
                "base_model": str(tmp_path / "base.mph"),
                "out_dir": str(tmp_path / "out1"),
                "case_id": "C_rtol1e4_max005",
                "save_mph": True,
                "min_free_ram_gb": 22.0,
                "min_free_disk_e_gb": 80.0,
            },
            {
                "job_id": "Q2",
                "priority": 2,
                "status": "READY",
                "base_model": str(tmp_path / "base.mph"),
                "out_dir": str(tmp_path / "out2"),
                "case_id": "B_rtol1e3",
                "save_mph": False,
                "min_free_ram_gb": 18.0,
                "min_free_disk_e_gb": 80.0,
            },
        ],
    }
    qp = tmp_path / "queue.json"
    qp.write_text(json.dumps(q), encoding="utf-8")
    return qp


def test_summarize_slots_counts_single_and_dual_socket():
    live = [
        {"pid": 1, "np": 16, "numasets": 1, "numafirst": 0, "out_mph": r"E:\a.mph"},
        {"pid": 2, "np": 16, "numasets": 1, "numafirst": 0, "out_mph": r"E:\b.mph"},
        {"pid": 3, "np": 16, "numasets": 1, "numafirst": 1, "out_mph": r"E:\c.mph"},
    ]
    s = db.summarize_slots(live, max_per_socket=2)
    assert s["socket_0_count"] == 2
    assert s["socket_1_count"] == 1
    assert s["free_sockets"] == [1]

    dual = [{"pid": 9, "np": 32, "numasets": 2, "numafirst": 0, "out_mph": r"E:\d.mph"}]
    s2 = db.summarize_slots(dual, max_per_socket=2)
    assert s2["socket_0_count"] == 1
    assert s2["socket_1_count"] == 1


def test_select_next_job_skips_completed_and_low_ram(tmp_path: Path):
    qp = _sample_queue(tmp_path)
    qdoc = json.loads(qp.read_text(encoding="utf-8"))
    # Q1 requires 22 GB RAM; at 20 GB free, Q1 is skipped and Q2 (18 GB) is chosen
    nxt, skipped = db.select_next_job(
        qdoc,
        health={"free_ram_gb": 20.0, "free_disk_e_gb": 200.0},
        live_procs=[],
    )
    assert nxt is not None and nxt["job_id"] == "Q2"
    assert any(s["job_id"] == "Q1" and "insufficient_ram" in s["reason"] for s in skipped)


def test_dispatch_dry_run_targets_free_socket(tmp_path: Path, monkeypatch):
    qp = _sample_queue(tmp_path)
    monkeypatch.setattr(db, "get_system_health", lambda _d="E:\\": {"free_ram_gb": 60.0, "total_ram_gb": 128.0, "free_disk_e_gb": 300.0})
    monkeypatch.setattr(
        db,
        "list_live_comsol_processes",
        lambda: [
            {"pid": 10, "np": 16, "numasets": 1, "numafirst": 0, "out_mph": r"E:\x.mph"},
            {"pid": 11, "np": 16, "numasets": 1, "numafirst": 0, "out_mph": r"E:\y.mph"},
            {"pid": 12, "np": 16, "numasets": 1, "numafirst": 1, "out_mph": r"E:\z.mph"},
        ],
    )
    res = db.dispatch_one_job(queue_path=qp, dry_run=True)
    assert res["status"] == "DRY_RUN_WOULD_LAUNCH"
    assert res["job_id"] == "Q1"
    assert res["target_socket"] == 1
    assert "16:1:1:throughput" in res["cmd"]
