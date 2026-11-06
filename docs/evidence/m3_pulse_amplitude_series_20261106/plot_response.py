"""Plot archived ROI response statistics, without loading models or field arrays."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    here = Path(__file__).resolve().parent
    inputs = [
        (0.0025, here / "analysis/a0025/field_comparison.json", "#0072B2", "-"),
        (0.005, here.parent / "m3_branch_pulse_20261105/field_comparison.json", "#D55E00", "--"),
        (0.010, here / "analysis/a0100/field_comparison.json", "#009E73", "-."),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), layout="constrained")
    peaks = []
    for amplitude, path, color, style in inputs:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["status"] != "PASS":
            raise ValueError(f"Unvalidated input: {path}")
        times = np.asarray(data["times"])
        response = np.asarray(data["response"]["roi_velocity_per_frame_over_U"])
        axes[0].plot(times, 100 * response, style, color=color,
                     label=f"A = {amplitude:g} m/s²" + (" (reused)" if amplitude == .005 else ""))
        peaks.append(100 * data["response"]["peak_roi_over_U"])
    noise = 100 * data["response"]["noise_floor_over_U"]
    axes[0].axhline(noise, color="#666666", linestyle=":", label="Z−B maximum ROI difference")
    axes[0].axvspan(181, 183, color="#dddddd", alpha=.5)
    axes[0].set(xlabel="Absolute time (s)", ylabel="ROI velocity RMS(P−Z) / U∞ (%)",
                title="Response trajectories; pulse at 181–183 s")
    axes[0].legend(fontsize=8)
    amplitudes = [item[0] for item in inputs]
    axes[1].plot(amplitudes, peaks, "o-", color="#0072B2")
    axes[1].axhline(100 * data["response"]["required_peak_over_U"], color="#666666",
                    linestyle=":", label="Frozen minimum response")
    for x, y in zip(amplitudes, peaks, strict=True):
        axes[1].annotate(f"{y:.3f}%", (x, y), xytext=(4, 7), textcoords="offset points")
    axes[1].set(xlabel="Acceleration amplitude (m/s²)", ylabel="Peak ROI response / U∞ (%)",
                title="Frozen response window: (181, 186] s", ylim=(0, max(peaks) * 1.18))
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle("Same native state and pulse envelope; amplitude is the only physical change")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
