"""Per-execution subprocess recording without changing process-global functions."""
from __future__ import annotations

import json
import locale
import subprocess
import threading
import time
import traceback
from pathlib import Path

from .evidence_io import persist_vectors, write_json


class ProcessRecorder:
    """A subprocess.run-compatible callable and context retaining original bytes.

    Each instance owns its output directory, counters and underlying runner. No
    global patch or thread-local override is installed. Bridge compilation and
    both launch paths receive this callable explicitly.
    """

    def __init__(self, directory, process_runner=None):
        self.directory = Path(directory)
        self.run = subprocess.run if process_runner is None else process_runner
        self._records = {}
        self._next_index = 0
        self._lock = threading.Lock()
        self._closed = False
        if (self.directory / "processes.json").exists():
            raise FileExistsError(self.directory / "processes.json")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.close()

    def close(self):
        with self._lock:
            if not self._closed:
                write_json(self.directory / "processes.json",
                           [self._records[index] for index in sorted(self._records)])
                self._closed = True

    def _retain(self, index, command, started, stdout, stderr, returncode, error=None):
        stem = f"process_{index:03d}"
        for channel, raw in (("stdout", stdout), ("stderr", stderr)):
            if raw is not None and not isinstance(raw, bytes):
                raise TypeError("The recorded process runner must return binary stdout/stderr")
            with (self.directory / f"{stem}_{channel}.log").open("xb") as handle:
                handle.write(raw or b"")
        record = {"command": command if isinstance(command, str) else [str(value) for value in command],
                  "returncode": returncode, "started_unix": started, "finished_unix": time.time(),
                  "stdout": f"{stem}_stdout.log", "stderr": f"{stem}_stderr.log"}
        if error is not None:
            record["error"] = {"type": type(error).__name__, "message": str(error),
                               "traceback": traceback.format_exc()}
        write_json(self.directory / f"{stem}_execution.json", record)
        with self._lock:
            self._records[index] = record
        decoded = (stdout or b"").decode("utf-8", errors="replace")
        if "<<<JSON_START>>>" not in decoded or "<<<JSON_END>>>" not in decoded:
            return
        try:
            payload = json.loads(decoded.split("<<<JSON_START>>>", 1)[1].split("<<<JSON_END>>>", 1)[0])
            write_json(self.directory / f"{stem}_payload.json", payload)
        except (ValueError, TypeError) as exc:
            # Never replace the bridge's typed error with a recorder JSON error.
            write_json(self.directory / f"{stem}_payload_decode_failure.json", {"error": str(exc)})
            return
        if not isinstance(payload, dict):
            return
        for field, suffix in (("partial_native_vectors", "partial"),
                              ("partial_native_derivatives", "partial_derivative")):
            if payload.get(field):
                try:
                    persist_vectors(self.directory, payload[field], prefix=f"{stem}_{suffix}")
                except Exception as exc:
                    write_json(self.directory / f"{stem}_{suffix}_decode_failure.json", {"error": str(exc)})

    def __call__(self, command, **kwargs):
        with self._lock:
            if self._closed:
                raise RuntimeError("Process recorder is closed")
            index = self._next_index
            self._next_index += 1
        started = time.time()
        text_mode = kwargs.pop("text", False)
        text_mode = kwargs.pop("universal_newlines", False) or text_mode
        encoding = kwargs.pop("encoding", None)
        requested_errors = kwargs.pop("errors", None)
        errors = requested_errors or "strict"
        text_mode = bool(text_mode or encoding is not None or requested_errors is not None)
        decoder = encoding or locale.getpreferredencoding(False)
        if text_mode and isinstance(kwargs.get("input"), str):
            kwargs["input"] = kwargs["input"].encode(decoder, errors=errors)
        try:
            process = self.run(command, **kwargs)
        except Exception as exc:
            self._retain(index, command, started, getattr(exc, "stdout", None),
                         getattr(exc, "stderr", None), getattr(exc, "returncode", None), exc)
            if text_mode and isinstance(exc, subprocess.CalledProcessError):
                if exc.stdout is not None:
                    exc.stdout = exc.stdout.decode(decoder, errors=errors)
                if exc.stderr is not None:
                    exc.stderr = exc.stderr.decode(decoder, errors=errors)
            raise
        self._retain(index, command, started, process.stdout, process.stderr, process.returncode)
        if text_mode:
            return subprocess.CompletedProcess(
                process.args, process.returncode,
                process.stdout.decode(decoder, errors=errors) if process.stdout is not None else None,
                process.stderr.decode(decoder, errors=errors) if process.stderr is not None else None,
            )
        return process
