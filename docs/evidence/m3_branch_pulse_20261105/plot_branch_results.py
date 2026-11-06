"""Render the archived experiment's actual comparison curves and response fields."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from hydro_disturb_bench.field_sequence import load_full_field_sequence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    metrics = json.loads((args.run / "analysis/field_comparison.json").read_text(encoding="utf8"))
    plan = json.loads((args.run / "preregistered_plan.json").read_text(encoding="utf8"))
    force = json.loads((args.run / "pulse/readback.json").read_text(encoding="utf8"))["force_evaluation"]
    times = np.asarray(metrics["times"])
    restart, response = metrics["restart"], metrics["response"]
    fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True, constrained_layout=True)
    axes[0].plot(force["actual_times"], force["integral"]["Fy_per_depth"], color="#8b4598", label="Applied source (native COMSOL integral)")
    axes[0].set_ylabel("Force per depth [N/m]")
    axes[1].plot(times, restart["velocity"]["per_frame"], label="Zero - baseline: velocity / U", color="#17699b")
    axes[1].plot(times, restart["pressure_centered"]["per_frame"], label="Zero - baseline: centered pressure / rho U^2", color="#ac6200", linestyle="--")
    axes[1].set_ylabel("Normalized spatial RMS")
    axes[2].plot(times, response["roi_velocity_per_frame_over_U"], label="Pulse - zero: ROI velocity / U", color="#aa3377")
    axes[2].plot(times, restart["roi_velocity_error_per_frame"], label="Zero - baseline: ROI velocity / U", color="#17699b", linestyle="--")
    axes[2].hlines(response["required_peak_over_U"], 181, 186, color="black", linestyle=":", label="Required peak in (181, 186] s")
    axes[2].set_ylabel("ROI velocity RMS / U")
    axes[2].set_xlabel("Absolute solution time [s]")
    for ax in axes:
        ax.axvspan(181, 183, color="#777777", alpha=.12)
        ax.grid(alpha=.2)
        ax.legend(loc="upper right", fontsize=8)
    fig.suptitle(f"M3 native-state branch prototype — field gates: {metrics['status']}")
    fig.savefig(args.output / "restart_and_response.png", dpi=180)
    plt.close(fig)
    zero = load_full_field_sequence(args.run / "sample_zero/fields")
    pulse = load_full_field_sequence(args.run / "sample_pulse/fields")
    selected = [int(np.flatnonzero(np.abs(times - t) < 1e-12)[0]) for t in (182, 184, 186)]
    differences = (pulse["values"][selected, 1] - zero["values"][selected, 1]) / plan["scales"]["U_inf_m_s"]
    limit = float(np.nanmax(np.abs(differences))) or 1
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), sharex=True, sharey=True, constrained_layout=True)
    for ax, index, field in zip(axes, selected, differences):
        artist = ax.pcolormesh(zero["x"], zero["y"], field, cmap="RdBu_r", vmin=-limit, vmax=limit, shading="nearest")
        ax.add_patch(plt.Circle((.1, .12), .005, color="#444444"))
        ax.set_title(f"t = {times[index]:g} s")
        ax.set_xlabel("x [m]")
        ax.set_aspect("equal")
    axes[0].set_ylabel("y [m]")
    fig.colorbar(artist, ax=axes, label="(v_pulse - v_zero) / U", shrink=.75)
    fig.suptitle("Controlled response on the same M3 mesh (161 x 97 sampling grid)")
    fig.savefig(args.output / "transverse_response.png", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    main()
