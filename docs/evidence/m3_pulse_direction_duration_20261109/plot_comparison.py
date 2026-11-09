"""Plot the archived common-window metrics; does not read models or tensors."""
from pathlib import Path
import argparse
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = json.loads(Path(__file__).with_name("comparison.json").read_text(encoding="utf-8"))
    fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True, constrained_layout=True)
    labels = {"default": "+90 deg, 2.0 s (reused)", "reverse": "-90 deg, 2.0 s",
              "short": "+90 deg, 1.2 s", "long": "+90 deg, 3.2 s"}
    colors = {"default": "#555555", "reverse": "#3264ad", "short": "#bc6720", "long": "#39836b"}
    styles = {"default": "--", "reverse": "-", "short": "-.", "long": ":"}
    for name, case in report["cases"].items():
        axes[0].plot(report["times"], [100*x for x in case["roi_velocity_per_frame_over_U"]],
                     label=labels[name], color=colors[name], linestyle=styles[name], linewidth=1.8)
    noise = report["cases"]["default"]["noise_floor_over_U"] * 100
    axes[0].axhline(noise, color="#777777", linewidth=0.8, label="Restart noise maximum")
    for name, case in report["against_default"].items():
        axes[1].plot(report["times"], [100*x for x in case["roi_velocity_per_frame_over_U"]],
                     label=labels[name], color=colors[name], linestyle=styles[name], linewidth=1.8)
    for ax in axes:
        ax.axvspan(181, 188, color="#aaaaaa", alpha=0.12, label="Common peak window (181,188] s")
        ax.grid(alpha=0.2)
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=8, loc="upper right", ncol=2)
    axes[0].set_ylabel("ROI RMS(variant - zero) / U [%]")
    axes[1].set_ylabel("ROI RMS(variant - default) / U [%]")
    axes[1].set_xlabel("Saved solution time [s]")
    axes[1].set_xlim(180, 200)
    axes[0].set_title("M3 pulse direction and duration: same checkpoint, grid and sampling")
    fig.savefig(args.output, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
