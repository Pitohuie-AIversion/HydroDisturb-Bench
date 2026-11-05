"""Preview two Cartesian samples of the saved M3 solution without overwriting output."""

from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path

import matplotlib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from hydro_disturb_bench.field_sampling import load_full_field_sample


def plot_sample(sample_dir: Path, output: Path) -> None:
    """Read validated NPZ samples and write one new PNG using common channel scales."""
    sample_dir, output = sample_dir.resolve(), output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Preview output already exists: {output}")
    if output.suffix.lower() != ".png":
        raise ValueError("Preview output must have a .png suffix")
    report = json.loads((sample_dir / "sample_report.json").read_text(encoding="utf-8"))
    if report.get("status") != "PASS" or len(report.get("grids", [])) != 2:
        raise ValueError("Preview requires a completed sample containing two resolutions")
    samples = []
    for item in report["grids"]:
        grid_dir = (sample_dir / item["path"]).resolve()
        if grid_dir.parent != sample_dir:
            raise ValueError("Grid directory must belong to the supplied sample")
        samples.append(load_full_field_sample(grid_dir))
    samples.sort(key=lambda sample: len(sample["x"]) * len(sample["y"]))
    channels = samples[0]["metadata"]["channel_order"]
    units = samples[0]["metadata"]["units"]
    coordinate_unit = samples[0]["metadata"]["coordinates"]["unit"]
    actual_times = [float(sample["times"][-1]) for sample in samples]
    if not np.isclose(actual_times[0], actual_times[1], rtol=0, atol=1e-12):
        raise ValueError("Both resolutions must have the same final actual solution time")
    for sample in samples[1:]:
        metadata = sample["metadata"]
        if (metadata["channel_order"] != channels or metadata["units"] != units
                or metadata["coordinates"]["unit"] != coordinate_unit):
            raise ValueError("Both samples must report matching channels and physical units")

    fig, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
    cmap = plt.get_cmap("coolwarm").copy()
    cmap.set_bad("#8a8a8a")
    try:
        for channel_index, (channel, unit) in enumerate(zip(channels, units)):
            finite_values = np.concatenate([
                sample["values"][-1, channel_index][~sample["mask"][-1]]
                for sample in samples
            ])
            vmin, vmax = float(finite_values.min()), float(finite_values.max())
            if vmin == vmax:
                padding = max(abs(vmin) * .01, 1e-12)
                vmin, vmax = vmin - padding, vmax + padding
            for row, sample in enumerate(samples):
                ax = axes[row, channel_index]
                x, y = sample["x"], sample["y"]
                field = np.ma.array(sample["values"][-1, channel_index], mask=sample["mask"][-1])
                image = ax.pcolormesh(x, y, field, shading="nearest", cmap=cmap,
                                     vmin=vmin, vmax=vmax, rasterized=True)
                ax.set(xlim=(x[0], x[-1]), ylim=(y[0], y[-1]), aspect="equal",
                       xlabel=f"x [{coordinate_unit}]", ylabel=f"y [{coordinate_unit}]",
                       title=f"{channel}: {len(x)} × {len(y)} sample points")
                ax.tick_params(labelsize=9)
            fig.colorbar(image, ax=axes[:, channel_index].tolist(), shrink=.82,
                         label=f"{channel} [{unit}]", pad=.025)
        fig.suptitle(f"Saved M3 solution | actual t = {actual_times[0]:.12g} s\n"
                     "Sampling grid (not solver mesh)", fontsize=16)
        fig.supxlabel("Gray: masked cylinder samples  •  Shared color limits within each channel", fontsize=11)
        buffer = BytesIO()
        fig.savefig(buffer, format="png", dpi=180, facecolor="white")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(buffer.getvalue())
    finally:
        plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_dir", type=Path, help="Completed sample root containing sample_report.json")
    parser.add_argument("--output", type=Path, required=True, help="New PNG destination; existing files are refused")
    args = parser.parse_args()
    plot_sample(args.sample_dir, args.output)
    print(f"Saved field preview: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
