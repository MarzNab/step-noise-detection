"""
Plot channel vs step-noise metrics from all RunResults JSON files in results/.

Three subplots:
  1. Channel vs amplitude (mV)
  2. Channel vs total_time_s
  3. Channel vs dominant_freq_hz

Each point is one (run, channel, block) aggressor entry. Runs are colored
by run_id so you can see patterns across files.
"""
import json
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


def load_rows(results_dir: Path):
    """Yield (run_id, ch, block_key, amplitude, total_time_s, dom_freq) tuples."""
    for p in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        run_id = data.get("run_id", p.stem)
        for ch_str, blocks in data.get("aggressors", {}).items():
            try:
                ch = int(ch_str)
            except ValueError:
                continue
            for bkey, agg in blocks.items():
                yield (run_id, ch, bkey,
                       agg.get("amplitude"),
                       agg.get("total_time_s"),
                       agg.get("dominant_freq_hz"))


def main(results_dir: str = "results"):
    rd = Path(results_dir)
    if not rd.exists():
        print(f"No such directory: {rd}")
        sys.exit(1)

    rows = list(load_rows(rd))
    if not rows:
        print(f"No step-noise entries found in {rd}/*.json")
        sys.exit(1)

    print(f"Loaded {len(rows)} step-noise entries from {rd}")

    # Assign a color per unique run_id
    run_ids = sorted({r[0] for r in rows})
    cmap = plt.get_cmap("tab20")
    color_of = {rid: cmap(i % 20) for i, rid in enumerate(run_ids)}
    print(f"Unique runs: {len(run_ids)}")

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)

    # Top: Amplitude
    for run_id, ch, _, amp, _, _ in rows:
        if amp is None:
            continue
        axes[0].scatter(ch, amp, c=[color_of[run_id]], s=30,
                        alpha=0.7, edgecolor="k", linewidth=0.3)
    axes[0].set_ylabel("Amplitude (mV)")
    axes[0].set_title("Step noise per channel — amplitude (peak-to-peak)")
    axes[0].grid(True, alpha=0.3)

    # Middle: Duration
    for run_id, ch, _, _, dur, _ in rows:
        if dur is None:
            continue
        axes[1].scatter(ch, dur, c=[color_of[run_id]], s=30,
                        alpha=0.7, edgecolor="k", linewidth=0.3)
    axes[1].set_ylabel("Total time (s)")
    axes[1].set_title("Step noise per channel — total duration")
    axes[1].grid(True, alpha=0.3)

    # Bottom: Frequency
    for run_id, ch, _, _, _, freq in rows:
        if freq is None:
            continue
        axes[2].scatter(ch, freq, c=[color_of[run_id]], s=30,
                        alpha=0.7, edgecolor="k", linewidth=0.3)
    axes[2].set_ylabel("Dominant freq (Hz)")
    axes[2].set_xlabel("Channel")
    axes[2].set_title("Step noise per channel — dominant frequency")
    axes[2].grid(True, alpha=0.3)
    axes[2].set_xlim(0, 257)

    # Legend (by run_id) — compact, right of the figure
    handles = [Line2D([0], [0], marker="o", color="w",
                       markerfacecolor=color_of[rid], markeredgecolor="k",
                       markersize=8, label=rid[:50])
               for rid in run_ids]
    fig.legend(handles=handles, loc="center right", fontsize=7,
               bbox_to_anchor=(1.0, 0.5), title="Run")

    plt.tight_layout(rect=[0, 0, 0.82, 1])

    # Save and show
    out_path = rd / "step_noise_summary.png"
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    print(f"Saved: {out_path}")
    plt.show()


if __name__ == "__main__":
    dirname = sys.argv[1] if len(sys.argv) > 1 else "results"
    main(dirname)
