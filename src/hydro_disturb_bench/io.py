"""Durable local JSON writes; never silently overwrite a case manifest."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_json(path: str | Path, data: Any) -> None:
    target=Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")
    fd,tmp=tempfile.mkstemp(prefix=f".{target.name}.",suffix=".tmp",dir=str(target.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        # Exclusive creation at final path. Avoid replacing a concurrently created output.
        os.link(tmp,target)
        if os.name != "nt":
            dir_fd=os.open(str(target.parent),os.O_RDONLY)
            try: os.fsync(dir_fd)
            finally: os.close(dir_fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
