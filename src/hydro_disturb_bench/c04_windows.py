"""C04 spatial-temporal world model data window indexing and learner views.

Supports both C04 2D slice sequences (T, 4, Ny, Nx) and C04 3D volume sequences
(T, 4, Nz, Ny, Nx) with cell-centered coordinates, solid masks, and signed
distance fields (SDF).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .comsol_java.errors import InvalidParameterError, ModelValidationError
from .sampling_3d import (
    C04_CHANNELS,
    C04_UNITS,
    load_c04_dataset,
)


@dataclass(frozen=True)
class C04WindowSpec:
    """Specification for sliding time windows across a C04 sequence."""

    history_frames: int
    forecast_frames: int
    stride: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.history_frames, int) or self.history_frames < 1:
            raise InvalidParameterError(f"history_frames must be >= 1, got {self.history_frames}")
        if not isinstance(self.forecast_frames, int) or self.forecast_frames < 1:
            raise InvalidParameterError(f"forecast_frames must be >= 1, got {self.forecast_frames}")
        if not isinstance(self.stride, int) or self.stride < 1:
            raise InvalidParameterError(f"stride must be >= 1, got {self.stride}")

    @property
    def window_length(self) -> int:
        """Total frames in a window (history + forecast)."""
        return self.history_frames + self.forecast_frames


@dataclass(frozen=True)
class C04WindowEntry:
    """Catalog entry identifying a single window within a C04 dataset."""

    dataset_path: str
    window_id: str
    start_index: int
    history_stop: int
    future_stop: int
    history_times: list[float]
    future_times: list[float]
    is_3d: bool
    spatial_shape: list[int]


class C04WindowIndex:
    """Catalog of all extractable sliding windows across one or multiple C04 sequences."""

    def __init__(self, spec: C04WindowSpec) -> None:
        self.spec = spec
        self.windows: list[C04WindowEntry] = []
        self._datasets_cache: dict[str, dict[str, Any]] = {}

    def add_dataset(self, dataset_path: Path | str, cache: bool = True) -> list[C04WindowEntry]:
        """Index all valid sliding windows from a C04 dataset archive."""
        path = Path(dataset_path).resolve()
        ds = load_c04_dataset(path)
        if cache:
            self._datasets_cache[str(path)] = ds

        fields = ds["fields"]
        times = ds["times"]
        meta = ds["metadata"]

        total_frames = len(times)
        if total_frames < self.spec.window_length:
            raise ModelValidationError(
                f"Dataset {path.name} has only {total_frames} frames, "
                f"insufficient for window length {self.spec.window_length} "
                f"({self.spec.history_frames} history + {self.spec.forecast_frames} forecast)"
            )

        # Verify time strictly increasing
        time_diffs = np.diff(times)
        if np.any(time_diffs <= 0):
            raise ModelValidationError(
                f"Dataset {path.name} times are not strictly increasing: {times}"
            )

        is_3d = bool(meta.get("is_3d", fields.ndim == 5))
        spatial_shape = list(fields.shape[2:])

        new_entries: list[C04WindowEntry] = []
        max_start = total_frames - self.spec.window_length
        for start_idx in range(0, max_start + 1, self.spec.stride):
            hist_stop = start_idx + self.spec.history_frames
            fut_stop = hist_stop + self.spec.forecast_frames

            hist_times = [float(t) for t in times[start_idx:hist_stop]]
            fut_times = [float(t) for t in times[hist_stop:fut_stop]]

            entry = C04WindowEntry(
                dataset_path=str(path),
                window_id=f"{path.stem}_w{start_idx:04d}",
                start_index=start_idx,
                history_stop=hist_stop,
                future_stop=fut_stop,
                history_times=hist_times,
                future_times=fut_times,
                is_3d=is_3d,
                spatial_shape=spatial_shape,
            )
            new_entries.append(entry)
            self.windows.append(entry)

        return new_entries

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        entry = self.windows[idx]
        return self.read_window(entry)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for entry in self.windows:
            yield self.read_window(entry)

    def read_window(self, entry: C04WindowEntry) -> dict[str, Any]:
        """Load the concrete learner view arrays for an indexed window."""
        ds_path = entry.dataset_path
        if ds_path in self._datasets_cache:
            ds = self._datasets_cache[ds_path]
        else:
            ds = load_c04_dataset(ds_path)

        fields = ds["fields"]
        times = ds["times"]
        solid_mask = ds["solid_mask"]
        sdf = ds["sdf"]
        meta = ds["metadata"]

        h_start = entry.start_index
        h_stop = entry.history_stop
        f_stop = entry.future_stop

        history = fields[h_start:h_stop]
        future = fields[h_stop:f_stop]
        history_times = times[h_start:h_stop]
        future_times = times[h_stop:f_stop]

        return {
            "window_id": entry.window_id,
            "is_3d": entry.is_3d,
            "channels": list(C04_CHANNELS),
            "units": list(C04_UNITS),
            "history": history,
            "future": future,
            "history_times": history_times,
            "future_times": future_times,
            "solid_mask": solid_mask,
            "sdf": sdf,
            "metadata": meta,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": asdict(self.spec),
            "total_windows": len(self.windows),
            "windows": [asdict(w) for w in self.windows],
        }

    def save_manifest(self, output_path: Path | str) -> Path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return out
