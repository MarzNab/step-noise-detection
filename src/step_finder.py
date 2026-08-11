"""
Step Noise Finder — scans all channels in a TDD baseline file and locates
regions with periodic step noise (4-20 Hz, configurable amplitude).

Standalone GUI application.  Reuses tdd_reader.py from the same src/ folder.
"""
__version__ = "1.3.0"

# Victims whose |r| is below this are not trustworthy coupling estimates:
# a channel dominated by its own noise projects a large but meaningless
# ratio. Gate the Ratio metric (and the trend) on it.
#
# Calibrated 2026-07-29 against 32 kHz ground truth (5,853 victims):
#   gate 0.20 -> 13% median error, 1131 victims kept
#   gate 0.25 -> 11% median error,  706 victims kept   <- knee
#   gate 0.30 -> 10% median error,  474 victims kept
#   gate 0.40 ->  9% median error,  217 victims kept
# Accuracy plateaus above ~0.30, so 0.25 is the best accuracy/coverage
# trade. Note the aggregate bias is ~1.07 at every gate: medians and
# trends are unbiased even ungated; the gate protects PER-CHANNEL values.
R_GATE = 0.25

import sys
import os
import glob
import fnmatch
import hashlib
import inspect
import json
import threading
from datetime import datetime

# PyInstaller windowed-app guard
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks, medfilt
from scipy.fft import rfft, irfft, next_fast_len
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from tdd_reader import (parse_header, load_data, descramble_channels,
                        channel_seq_index, TDDHeader)


# ── Detection core ────────────────────────────────────────────────────────────

def _bandpass(data: np.ndarray, fs: float, lo: float, hi: float, order: int = 2,
              filter_coefs: tuple | None = None):
    """Zero-phase Butterworth bandpass. Accepts pre-computed (b, a) for speed."""
    if filter_coefs is None:
        nyq = fs / 2.0
        b, a = butter(order, [lo / nyq, hi / nyq], btype="band")
    else:
        b, a = filter_coefs
    return filtfilt(b, a, data)


def compute_bandpass_coefs(sample_rate: float, freq_lo: float, freq_hi: float,
                            order: int = 2):
    """Return (b, a) matching what find_step_noise would use internally."""
    nyq = sample_rate / 2.0
    lo = max(0.5, freq_lo - 1.0) / nyq
    hi = min(sample_rate / 2 - 1, freq_hi + 5.0) / nyq
    return butter(order, [lo, hi], btype="band")


def find_step_noise(
    channel_uv: np.ndarray,
    sample_rate: float,
    freq_lo: float = 4.0,
    freq_hi: float = 20.0,
    min_amplitude_mv: float = 100.0,
    min_periodicity: float = 0.2,
    window_sec: float = 10.0,
    overlap: float = 0.5,
    max_gap_sec: float = 30.0,
    filter_coefs: tuple | None = None,
    envelope_mode: bool = False,
    min_peaks_in_window: int = 10,
):
    """
    Detect time segments with periodic step noise.

    Two detection modes:
      Periodic (default): autocorrelation-based, requires the signal to
        repeat itself (good for clean ~10 Hz step noise).
      envelope_mode=True: counts peaks in the bandpass signal; flags any
        window with sustained activity in the band, even if not strictly
        periodic. Frequency is reported as peaks / window_sec.

    filter_coefs: optional pre-computed (b, a) from compute_bandpass_coefs().
        When scanning many channels at the same rate, pass this in to skip
        re-designing the Butterworth filter per channel.

    Returns a list of dicts with keys:
        start_sec, end_sec, frequency_hz, amplitude_mv, periodicity
    """
    # Bandpass isolate the step-noise frequency range (with a little margin)
    filt = _bandpass(channel_uv, sample_rate,
                     max(0.5, freq_lo - 1.0), min(sample_rate / 2 - 1, freq_hi + 5.0),
                     filter_coefs=filter_coefs)

    window_n = int(window_sec * sample_rate)
    step_n = max(1, int(window_n * (1 - overlap)))
    min_lag = max(1, int(sample_rate / freq_hi))   # shortest period
    max_lag = int(sample_rate / freq_lo)            # longest period

    raw_hits = []

    for start in range(0, len(filt) - window_n + 1, step_n):
        seg = filt[start : start + window_n]

        # Amplitude gate — peak-to-peak of filtered signal
        pp_mv = (seg.max() - seg.min()) / 1000.0
        if pp_mv < min_amplitude_mv:
            continue

        peak_height = 0.2 * (pp_mv * 1000.0)   # 20% of pp, in µV

        if envelope_mode:
            # Envelope mode: skip autocorrelation; just require enough peaks
            # in the band so single transients don't pass.
            min_distance = max(1, int(sample_rate / freq_hi * 0.5))
            peaks, _ = find_peaks(seg, height=peak_height, distance=min_distance)
            if len(peaks) < min_peaks_in_window:
                continue
            # Frequency = peaks per second; clamp to detection band.
            freq = len(peaks) / window_sec
            if freq < freq_lo or freq > freq_hi:
                continue
            peak_ac = 0.0   # not measured in envelope mode
        else:
            # Autocorrelation via FFT (≈100× faster than np.correlate for 20k-sample windows)
            seg_n = seg - seg.mean()
            n_pad = next_fast_len(2 * len(seg_n) - 1, real=True)
            F = rfft(seg_n, n=n_pad)
            ac_full = irfft(F * F.conj(), n=n_pad)
            ac = ac_full[: len(seg_n)]          # positive lags only
            ac /= ac[0] + 1e-20                 # normalise

            ac_band = ac[min_lag : max_lag + 1]
            if len(ac_band) == 0:
                continue

            peak_ac = float(ac_band.max())
            if peak_ac < min_periodicity:
                continue

            peak_lag = int(np.argmax(ac_band)) + min_lag

            # Reject single level shifts: true periodic signals have
            # autocorrelation peaks at multiples of the fundamental lag.
            lag_2x = 2 * peak_lag
            if lag_2x < len(ac):
                ac_2nd = float(ac[lag_2x])
                if ac_2nd < 0.05:
                    continue
            else:
                continue

            freq = sample_rate / peak_lag

            # Reject single impulses / transients by counting actual oscillations.
            min_distance = max(1, int(peak_lag * 0.5))
            peaks, _ = find_peaks(seg, height=peak_height, distance=min_distance)
            expected_peaks = freq * window_sec
            if len(peaks) < 0.3 * expected_peaks:
                continue

        raw_hits.append(
            {
                "start_sec": start / sample_rate,
                "end_sec": (start + window_n) / sample_rate,
                "frequency_hz": round(freq, 1),
                "amplitude_mv": round(pp_mv, 1),
                "periodicity": round(peak_ac, 3),
            }
        )

    # Merge overlapping / adjacent windows into contiguous segments
    if not raw_hits:
        return []

    merged = [raw_hits[0].copy()]
    for h in raw_hits[1:]:
        prev = merged[-1]
        gap = h["start_sec"] - prev["end_sec"]
        if gap <= max_gap_sec:
            prev["end_sec"] = max(prev["end_sec"], h["end_sec"])
            prev["amplitude_mv"] = max(prev["amplitude_mv"], h["amplitude_mv"])
            prev["periodicity"] = max(prev["periodicity"], h["periodicity"])
            prev["frequency_hz"] = round(
                (prev["frequency_hz"] + h["frequency_hz"]) / 2, 1
            )
        else:
            merged.append(h.copy())

    # Refine segment boundaries — the 5 s window step gives coarse edges.
    # Walk outward from each end in 2 s sub-windows; commit the extension
    # whenever the local bandpass peak-to-peak amplitude is at least 30 %
    # of the segment's own measured amplitude AND at least the user-set
    # `min_amplitude_mv` floor. The user-amp floor guarantees the
    # reported event keeps amplitude above the threshold throughout —
    # otherwise the relative 30 % rule could leak edges into regions
    # well below the user's limit. Tolerate up to ~6 s of consecutive
    # quiet (3 sub-windows × 2 s) before stopping, so brief dips don't
    # truncate the event.
    sub_n = max(1, int(2.0 * sample_rate))
    max_misses = 3   # ~6 s of slack
    n_filt = len(filt)
    user_floor_uv = min_amplitude_mv * 1000.0
    for seg in merged:
        s = int(seg["start_sec"] * sample_rate)
        e = int(seg["end_sec"] * sample_rate)
        threshold_uv = max(0.3 * seg["amplitude_mv"] * 1000.0,
                           user_floor_uv)

        # Walk left, tolerating brief misses
        misses = 0
        last_good_s = s
        cand = s
        while cand - sub_n >= 0:
            chunk = filt[cand - sub_n : cand]
            if (chunk.max() - chunk.min()) < threshold_uv:
                misses += 1
                if misses > max_misses:
                    break
            else:
                misses = 0
                last_good_s = cand - sub_n
            cand -= sub_n
        s = last_good_s

        # Walk right, tolerating brief misses
        misses = 0
        last_good_e = e
        cand = e
        while cand + sub_n <= n_filt:
            chunk = filt[cand : cand + sub_n]
            if (chunk.max() - chunk.min()) < threshold_uv:
                misses += 1
                if misses > max_misses:
                    break
            else:
                misses = 0
                last_good_e = cand + sub_n
            cand += sub_n
        e = last_good_e

        seg["start_sec"] = s / sample_rate
        seg["end_sec"] = e / sample_rate

    # After refinement, segments that started apart may now overlap.
    # Sort and re-merge any that touch or are within max_gap_sec.
    merged.sort(key=lambda s: s["start_sec"])
    rerun = [merged[0]]
    for h in merged[1:]:
        prev = rerun[-1]
        if h["start_sec"] - prev["end_sec"] <= max_gap_sec:
            prev["end_sec"] = max(prev["end_sec"], h["end_sec"])
            prev["amplitude_mv"] = max(prev["amplitude_mv"], h["amplitude_mv"])
            prev["periodicity"] = max(prev["periodicity"], h["periodicity"])
            prev["frequency_hz"] = round(
                (prev["frequency_hz"] + h["frequency_hz"]) / 2, 1
            )
        else:
            rerun.append(h)
    merged = rerun

    # Compute baselines: max value of raw signal in a window just before
    # and just after each step-noise segment
    n_total = len(channel_uv)
    for seg in merged:
        # Pre-baseline: window before start
        pre_end = int(seg["start_sec"] * sample_rate)
        pre_start = max(0, pre_end - window_n)
        if pre_start < pre_end:
            seg["baseline_mv"] = round(float(channel_uv[pre_start:pre_end].max()) / 1000.0, 1)
        else:
            # No pre-window exists (event starts at t=0) — report N/A,
            # not 0, so downstream fits can't be skewed by a fake value.
            seg["baseline_mv"] = None

        # Post-baseline: window after end (if data exists)
        post_start = int(seg["end_sec"] * sample_rate)
        post_end = min(n_total, post_start + window_n)
        if post_start < post_end and post_start < n_total:
            seg["post_baseline_mv"] = round(float(channel_uv[post_start:post_end].max()) / 1000.0, 1)
        else:
            seg["post_baseline_mv"] = None

    return merged


# ── GUI ───────────────────────────────────────────────────────────────────────

class StepFinderApp:
    """Tkinter GUI that scans all channels for step noise."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"Step Noise Finder  v{__version__}")
        root.geometry("1280x820")
        root.minsize(960, 600)

        # State
        self.filepath: str | None = None
        self.header: TDDHeader | None = None
        self.data: np.ndarray | None = None
        self.chan_map: list = []
        self.results: list = []          # list of (cid, col, segments) per file
        self._scanning = False
        self._stop_event = threading.Event()
        self._batch_folder: str | None = None
        self._batch_files: list = []
        self._batch_label: str = ""
        # LRU file cache — bounded by total waveform bytes.
        # Files load fully while being scanned, then are evicted (oldest first)
        # once the 8 GB limit is reached. Results remain in the table/reports;
        # if the user selects a row whose file was evicted, _load_tdd reloads.
        self._file_cache: dict = {}      # filepath -> (header, data, chan_map)
        self._cache_bytes: int = 0
        self._MAX_CACHE_BYTES: int = 8 * 1024 ** 3   # 8 GB

        self._build_ui()
        if self._load_valid_stash() is not None:
            self._status("Ready — previous session available: "
                         "File → Resume Last Session.")
        else:
            self._status("Ready — open a TDD file to begin.")

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # Menu
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Open TDD…", command=self._open_file,
                              accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Scan Files…", command=self._scan_files)
        file_menu.add_command(label="Scan Folders…", command=self._scan_multi_folders)
        file_menu.add_command(label="Scan All Runs…", command=self._scan_data_root)
        file_menu.add_separator()
        file_menu.add_command(label="Write Reports Now",
                              command=self._write_reports_now)
        file_menu.add_separator()
        file_menu.add_command(label="Resume Last Session",
                              command=self._restore_stash)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menu.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menu)
        self.root.bind_all("<Control-o>", lambda e: self._open_file())
        self.root.bind_all("<Control-Shift-O>", lambda e: self._scan_folder())

        # Top bar — file info + parameters
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")

        ttk.Label(top, text="File:").pack(side="left")
        self.file_lbl = ttk.Label(top, text="(none)", width=60, anchor="w")
        self.file_lbl.pack(side="left", padx=(4, 16))

        ttk.Label(top, text="Amp ≥ (mV):").pack(side="left")
        self.amp_var = tk.DoubleVar(value=100.0)
        ttk.Spinbox(top, textvariable=self.amp_var, from_=10, to=5000,
                     increment=10, width=7).pack(side="left", padx=(2, 12))

        ttk.Label(top, text="Freq (Hz):").pack(side="left")
        self.freq_lo_var = tk.DoubleVar(value=4.0)
        ttk.Spinbox(top, textvariable=self.freq_lo_var, from_=1, to=49,
                     increment=1, width=5).pack(side="left", padx=2)
        ttk.Label(top, text="–").pack(side="left")
        self.freq_hi_var = tk.DoubleVar(value=20.0)
        ttk.Spinbox(top, textvariable=self.freq_hi_var, from_=2, to=49,
                     increment=1, width=5).pack(side="left", padx=(2, 12))

        ttk.Label(top, text="Periodicity ≥:").pack(side="left")
        self.per_var = tk.DoubleVar(value=0.2)
        ttk.Spinbox(top, textvariable=self.per_var, from_=0.05, to=0.95,
                     increment=0.05, width=5, format="%.2f").pack(side="left", padx=(2, 12))

        ttk.Label(top, text="Gap (s):").pack(side="left")
        self.gap_var = tk.DoubleVar(value=30.0)
        ttk.Spinbox(top, textvariable=self.gap_var, from_=0, to=300,
                     increment=5, width=5).pack(side="left", padx=(2, 12))

        self.envelope_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Envelope mode",
                        variable=self.envelope_var).pack(side="left", padx=(12, 4))

        self.scan_btn = ttk.Button(top, text="Scan All Channels",
                                   command=self._start_scan, state="disabled")
        self.scan_btn.pack(side="left", padx=4)

        self.folder_btn = ttk.Button(top, text="Scan Baselines…",
                                     command=self._scan_folder)
        self.folder_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(top, text="Stop",
                                   command=self._stop_scan, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.xtalk_btn = ttk.Button(top, text="Crosstalk",
                                    command=self._show_crosstalk)
        self.xtalk_btn.pack(side="left", padx=4)

        self.xtalk_all_btn = ttk.Button(top, text="Xtalk All Events",
                                        command=self._start_xtalk_all)
        self.xtalk_all_btn.pack(side="left", padx=(0, 4))

        self.xtalk_whole_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Whole event",
                        variable=self.xtalk_whole_var).pack(side="left",
                                                            padx=(0, 4))

        self.full_file_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Full file (top plot)",
                        variable=self.full_file_var,
                        command=self._on_select).pack(side="left", padx=(12, 4))

        # Progress
        prog_frame = ttk.Frame(self.root, padding=(6, 0))
        prog_frame.pack(fill="x")
        self.busy_lbl = tk.Label(prog_frame, text="", fg="red",
                                 font=("Segoe UI", 10, "bold"), width=14,
                                 anchor="w")
        self.busy_lbl.pack(side="left", padx=(0, 6))
        self.progress = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress.pack(fill="x", side="left", expand=True)
        self.prog_lbl = ttk.Label(prog_frame, text="", width=30)
        self.prog_lbl.pack(side="left", padx=6)

        # Animation state for the analyzing indicator
        self._busy_dots = 0
        self._busy_after_id = None

        # Main area — PanedWindow: results table (left) + plot (right)
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=6)

        # Results table
        tbl_frame = ttk.Frame(pane)
        pane.add(tbl_frame, weight=1)

        cols = ("file", "channel", "start", "end", "freq", "amp",
                "pre_baseline", "post_baseline", "periodicity", "xtalk")
        self.tree = ttk.Treeview(tbl_frame, columns=cols, show="headings",
                                 selectmode="browse")
        col_labels = {
            "file": "File",
            "channel": "Channel",
            "start": "Start (s)",
            "end": "End (s)",
            "freq": "Freq (Hz)",
            "amp": "Amp (mV)",
            "pre_baseline": "Pre BL (mV)",
            "post_baseline": "Post BL (mV)",
            "periodicity": "Periodicity",
            "xtalk": "Xtalk @Agg (%)",
        }
        self._col_labels = col_labels
        self._sort_state: dict = {}   # col -> reverse (bool)
        for cid, label in col_labels.items():
            self.tree.heading(cid, text=label,
                              command=lambda c=cid: self._sort_tree(c))
        for c in cols:
            self.tree.column(c, width=85, anchor="center")
        self.tree.column("file", width=180, anchor="w")
        self.tree.column("channel", width=70)
        self.tree.column("pre_baseline", width=90)
        self.tree.column("post_baseline", width=90)
        self.tree.column("xtalk", width=100)

        vsb = ttk.Scrollbar(tbl_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Tooltip: hover over the File column to see the full path
        self._tt_win = None
        self._tt_last_row = None
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", lambda _e: self._hide_tooltip())

        # Plot area
        plot_frame = ttk.Frame(pane)
        pane.add(plot_frame, weight=2)

        self.fig = Figure(figsize=(8, 5), dpi=96,
                          tight_layout={"pad": 1.5, "h_pad": 1.0})
        self.ax_raw = self.fig.add_subplot(2, 1, 1)
        self.ax_filt = self.fig.add_subplot(2, 1, 2)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        tb = NavigationToolbar2Tk(self.canvas, plot_frame)
        tb.update()

        # Status bar
        self.status_bar = ttk.Label(self.root, text="", relief="sunken",
                                    anchor="w", padding=2)
        self.status_bar.pack(fill="x", side="bottom")

        # Load saved settings (overrides spinbox defaults), then attach
        # change-listeners so any tweak gets persisted automatically.
        self._load_config()
        for var in (self.amp_var, self.freq_lo_var, self.freq_hi_var,
                    self.per_var, self.gap_var, self.envelope_var,
                    self.full_file_var):
            var.trace_add("write", lambda *_: self._save_config())

    # ── File-column tooltip ──────────────────────────────────────────────

    def _on_tree_motion(self, event):
        """Show a tooltip with the full path when hovering over the File column."""
        rowid = self.tree.identify_row(event.y)
        colid = self.tree.identify_column(event.x)
        if not rowid or colid != "#1":   # #1 is the "file" column
            self._hide_tooltip()
            return
        tags = self.tree.item(rowid, "tags")
        if not tags:
            self._hide_tooltip()
            return
        full = tags[0]   # first tag is the filepath (set in _scan_done)
        if rowid == self._tt_last_row and self._tt_win is not None:
            return       # same row — keep existing tooltip
        self._hide_tooltip()
        self._tt_last_row = rowid
        self._tt_win = tk.Toplevel(self.tree)
        self._tt_win.wm_overrideredirect(True)
        self._tt_win.wm_geometry(
            f"+{event.x_root + 15}+{event.y_root + 10}")
        tk.Label(self._tt_win, text=full, bg="#ffffe0",
                 relief="solid", borderwidth=1, padx=6, pady=2,
                 font=("Segoe UI", 9)).pack()

    def _hide_tooltip(self):
        if self._tt_win is not None:
            self._tt_win.destroy()
            self._tt_win = None
        self._tt_last_row = None

    # ── Crosstalk analysis ───────────────────────────────────────────────

    def _show_crosstalk(self):
        """Correlate the selected event window against all other channels."""
        sel = self.tree.selection()
        if not sel:
            self._status("Select a detection row first.")
            return
        item = self.tree.item(sel[0])
        vals = item["values"]
        agg_cid = int(vals[1])
        start_sec = float(vals[2])
        end_sec = float(vals[3])
        tags = item["tags"]
        filepath = tags[0]

        self._status(f"Computing crosstalk vs Ch {agg_cid} "
                     f"({start_sec:.0f}–{end_sec:.0f}s)…")
        threading.Thread(
            target=self._crosstalk_worker,
            args=(filepath, agg_cid, start_sec, end_sec,
                  self.xtalk_whole_var.get()),
            daemon=True,
        ).start()

    def _crosstalk_worker(self, filepath, agg_cid, start_sec, end_sec,
                          whole_event=False):
        try:
            header, data, chan_map = self._load_tdd(filepath)
        except Exception as exc:
            self.root.after(0, self._status, f"Crosstalk: load failed — {exc}")
            return

        fs = header.sample_rate

        # Coupling is measured around the center of the event, where the
        # step noise is in full swing. A longer window averages down
        # uncorrelated noise (SNR ~ sqrt(N)), which matters when the
        # coupling is small. "Whole event" uses the full span (capped at
        # 5 min, centered); otherwise the center 10 s.
        CORR_WINDOW_SEC = 300.0 if whole_event else 10.0
        center = (start_sec + end_sec) / 2.0
        half = min(CORR_WINDOW_SEC, end_sec - start_sec) / 2.0
        s0 = max(0, int((center - half) * fs))
        s1 = min(data.shape[0], int((center + half) * fs))
        if s1 - s0 < int(0.1 * fs):
            self.root.after(0, self._status, "Crosstalk: event too short.")
            return
        win_sec = (s1 - s0) / fs

        cid_to_col = {cm["cid"]: cm["col"] for cm in chan_map}
        agg_col = cid_to_col.get(agg_cid)
        if agg_col is None:
            self.root.after(0, self._status, "Crosstalk: channel not found.")
            return

        # Correlate the RAW signals (mean-removed only, no bandpass) so the
        # coupling measurement isn't shaped by the detection filter.
        agg = data[s0:s1, agg_col].astype(np.float64)
        agg -= agg.mean()

        def _stats(v):
            norm = float(np.sqrt((v ** 2).sum())) + 1e-20
            energy = float((v ** 2).sum()) + 1e-20
            pp = float(v.max() - v.min())
            return norm, energy, pp

        agg_norm, agg_energy, agg_pp = _stats(agg)

        # ±1 kernel from the aggressor: +1 where it is clearly high, -1
        # where clearly low, 0 in the dead zone near zero. Averaging
        # kernel × victim estimates the aggressor step amplitude present
        # in the victim; dividing by the kernel's response to the
        # aggressor itself gives a robust coupling gain that outlier
        # spikes on the victim barely move.
        def _make_kernel(v):
            thr = 0.25 * v.std()
            k = np.zeros_like(v)
            k[v > thr] = 1.0
            k[v < -thr] = -1.0
            return k, float(np.count_nonzero(k)) + 1e-20

        kern, kern_n = _make_kernel(agg)
        k_agg = float((kern * agg).sum()) / kern_n            # µV

        n_ch = header.channel_count or 256

        # Channels carrying their own large signal in this window are
        # co-aggressors, not victims: their ratio is inflated and they
        # contaminate the profile. Flag anything above 25% of the
        # aggressor's own peak-to-peak.
        win_pp = (data[s0:s1, :].max(axis=0) - data[s0:s1, :].min(axis=0))
        loud_thr = 0.25 * agg_pp
        loud_cids = sorted(
            cm["cid"] for cm in chan_map
            if cm["cid"] != agg_cid and win_pp[cm["col"]] > loud_thr)

        def _one(cmap):
            cid, col = cmap["cid"], cmap["col"]
            if cid == agg_cid:
                return None
            sig = data[s0:s1, col].astype(np.float64)
            sig -= sig.mean()

            # Correlation / least-squares projection
            sig_norm = float(np.sqrt((sig ** 2).sum())) + 1e-20
            dot = float((agg * sig).sum())
            r = dot / (agg_norm * sig_norm)
            beta = dot / agg_energy          # least-squares coupling gain
            corr = (r, abs(beta) * agg_pp / 1000.0, abs(beta) * 100.0)

            # kernel method
            g = (float((kern * sig).sum()) / kern_n) / (k_agg + 1e-20)
            kern_raw = (g, abs(g) * agg_pp / 1000.0, abs(g) * 100.0)

            return {
                "cid": cid,
                "seq": channel_seq_index(cid, n_ch),
                "corr": corr, "kern": kern_raw,
                "loud": cid in loud_set,
            }

        loud_set = set(loud_cids)
        results = []
        max_workers = min(os.cpu_count() or 1, 8)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for out in ex.map(_one, chan_map):
                if out is not None:
                    results.append(out)

        agg_seq = channel_seq_index(agg_cid, n_ch)
        max_r = max((abs(d["corr"][0]) for d in results
                     if not d["loud"]), default=0.0)
        self.root.after(0, self._show_crosstalk_results,
                        agg_cid, agg_seq, start_sec, end_sec,
                        agg_pp / 1000.0, win_sec, results,
                        loud_cids, max_r)

    def _show_crosstalk_results(self, agg_cid, agg_seq, start_sec, end_sec,
                                agg_pp_mv, win_sec, results,
                                loud_cids=(), max_r=1.0):
        self._status("Crosstalk analysis complete.")
        win = tk.Toplevel(self.root)
        win.title(f"Crosstalk — aggressor Ch {agg_cid} "
                  f"({start_sec:.0f}–{end_sec:.0f}s)")
        win.geometry("980x620")

        ttk.Label(win, padding=6, text=(
            f"Aggressor Ch {agg_cid} (physical position {agg_seq}): "
            f"{agg_pp_mv:.1f} mV pp (raw), center {win_sec:.0f} s of the "
            f"event. "
            f"Correlation = least-squares projection of the aggressor onto "
            f"each channel. Kernel ±1 = the aggressor quantized to "
            f"+1/0/−1, multiplied with each channel and averaged — robust "
            f"to spikes on the victims. Ratio = coupled amplitude / "
            f"aggressor amplitude. Physical order un-scrambles the "
            f"serpentine layout. Victims with |r| < {R_GATE:.2f} are "
            f"greyed out - their ratio is unreliable (mostly own noise)."
        ), wraplength=940).pack(anchor="w")

        # Measurability banner. If no victim's waveform demonstrably
        # follows the aggressor, every ratio here is noise - say so loudly
        # rather than letting a meaningless trend look like physics.
        if max_r < R_GATE:
            tk.Label(win, bg="#fdecea", fg="#8a1c13",
                     font=("Segoe UI", 10, "bold"), anchor="w",
                     padx=8, pady=5, justify="left",
                     text=(f"NOT MEASURABLE - best |r| is only {max_r:.2f} "
                           f"(gate {R_GATE:.2f}). The coupled signal is below "
                           f"every victim's own noise in this window, so the "
                           f"ratios and trends below are not meaningful. "
                           f"Try a different event or a longer window."),
                     wraplength=1180).pack(fill="x", padx=6)
        if loud_cids:
            shown = ", ".join(f"ch {c}" for c in loud_cids[:10])
            more = f" (+{len(loud_cids) - 10} more)" if len(loud_cids) > 10 else ""
            tk.Label(win, bg="#fff8e1", fg="#6b4e00",
                     font=("Segoe UI", 9), anchor="w", padx=8, pady=4,
                     justify="left",
                     text=(f"Co-aggressors excluded ({len(loud_cids)} "
                           f"channels above 25% of the aggressor amplitude): "
                           f"{shown}{more}. These carry their own signal, "
                           f"which would inflate their ratio."),
                     wraplength=1180).pack(fill="x", padx=6)

        pane = ttk.PanedWindow(win, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=6)

        # Table
        tbl_frame = ttk.Frame(pane)
        pane.add(tbl_frame, weight=1)
        cols = ("ch", "pos", "r", "pp", "pct")
        tv = ttk.Treeview(tbl_frame, columns=cols, show="headings")
        tv.heading("ch", text="Channel")
        tv.heading("pos", text="Pos")
        tv.heading("r", text="r / gain")
        tv.heading("pp", text="Xtalk Amp (mV)")
        tv.heading("pct", text="Ratio (%)")
        tv.column("ch", width=65, anchor="center")
        tv.column("pos", width=50, anchor="center")
        tv.column("r", width=95, anchor="center")
        tv.column("pp", width=95, anchor="center")
        tv.column("pct", width=75, anchor="center")
        sb = ttk.Scrollbar(tbl_frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Controls
        plot_frame = ttk.Frame(pane)
        pane.add(plot_frame, weight=2)

        ctrl = ttk.Frame(plot_frame)
        ctrl.pack(fill="x", pady=(0, 2))
        ttk.Label(ctrl, text="Method:").pack(side="left", padx=(4, 4))
        method_var = tk.StringVar(value="Correlation")
        for mode in ("Correlation", "Kernel ±1"):
            ttk.Radiobutton(ctrl, text=mode, value=mode,
                            variable=method_var,
                            command=lambda: _refresh()).pack(side="left",
                                                             padx=3)
        clip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl, text="Clip Y (95th pct)", variable=clip_var,
                        command=lambda: _refresh()).pack(side="left",
                                                         padx=(10, 4))
        trend_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctrl, text="Trend", variable=trend_var,
                        command=lambda: _refresh()).pack(side="left",
                                                         padx=(0, 4))
        excluded: set = set()

        def _exclude_selected():
            for iid in tv.selection():
                excluded.add(int(tv.item(iid)["values"][0]))
            _refresh()

        def _reset_excluded():
            excluded.clear()
            _refresh()

        ttk.Button(ctrl, text="Exclude Selected",
                   command=_exclude_selected).pack(side="left", padx=(10, 4))
        ttk.Button(ctrl, text="Reset",
                   command=_reset_excluded).pack(side="left", padx=(0, 4))

        ctrl2 = ttk.Frame(plot_frame)
        ctrl2.pack(fill="x", pady=(0, 2))
        ttk.Label(ctrl2, text="Y:").pack(side="left", padx=(4, 4))
        axis_var = tk.StringVar(value="|r|")
        for mode in ("|r|", "Xtalk Amp (mV)", "Ratio %"):
            ttk.Radiobutton(ctrl2, text=mode, value=mode,
                            variable=axis_var,
                            command=lambda: _refresh()).pack(side="left",
                                                             padx=3)
        ttk.Label(ctrl2, text="   X:").pack(side="left", padx=(8, 4))
        xaxis_var = tk.StringVar(value="Channel ID")
        for mode in ("Physical order", "Channel ID"):
            ttk.Radiobutton(ctrl2, text=mode, value=mode,
                            variable=xaxis_var,
                            command=lambda: _refresh()).pack(side="left",
                                                             padx=3)

        fig = Figure(figsize=(6, 5), dpi=96, tight_layout={"pad": 1.2})
        ax = fig.add_subplot(1, 1, 1)
        canvas = FigureCanvasTkAgg(fig, master=plot_frame)

        def _rows(gated=False):
            """Current-mode rows: (cid, seq, r_or_gain, xt_mv, pct).

            gated=True keeps only victims whose |r| >= R_GATE, i.e. whose
            waveform actually follows the aggressor. Ungated ratios are
            inflated for channels carrying their own step noise.
            """
            kernel = method_var.get() == "Kernel ±1"
            key = "kern" if kernel else "corr"
            out = [(d["cid"], d["seq"], *d[key]) for d in results
                   if d["cid"] not in excluded and not d["loud"]]
            if gated:
                out = [z for z in out if abs(z[2]) >= R_GATE]
            return out

        def _refresh():
            rows = _rows()
            kernel = method_var.get() == "Kernel ±1"
            # Table — sorted by |r or gain| descending
            tv.delete(*tv.get_children())
            tv.tag_configure("weak", foreground="#9aa3b2")
            for cid, seq, r, xt, pct in sorted(rows, key=lambda z: abs(z[2]),
                                               reverse=True):
                tags = () if abs(r) >= R_GATE else ("weak",)
                tv.insert("", "end", values=(cid, seq, f"{r:+.3f}",
                                             f"{xt:.2f}", f"{pct:.2f}"),
                          tags=tags)
            # Plot
            mode = axis_var.get()
            phys = xaxis_var.get() == "Physical order"
            cids = [z[0] for z in rows]
            xs = [z[1] if phys else z[0] for z in rows]
            ax.clear()
            if mode == "|r|":
                ys = [abs(z[2]) for z in rows]
                if kernel:
                    ax.set_ylabel("|kernel gain|")
                else:
                    ax.set_ylabel("|r|")
            elif mode == "Xtalk Amp (mV)":
                ys = [z[3] for z in rows]
                ax.set_ylabel("Xtalk Amp (mV pp)")
            else:
                ys = [z[4] for z in rows]
                ax.set_ylabel("Ratio (% of aggressor)")

            # Optional Y clipping limit: 95th percentile so one outlier
            # channel doesn't flatten the rest. Points above are drawn
            # at the limit; trends always use unclipped values.
            lim = None
            n_clipped = 0
            if clip_var.get() and ys:
                lim = 1.1 * float(np.percentile(ys, 95))
                if lim <= 0:
                    lim = None

            # Points + rolling-median trend, split by channel parity —
            # odd and even channels sit on opposite sides of the
            # serpentine array, so their coupling can differ.
            def _plot_group(parity, pt_color, tr_color, label):
                clipped = 0
                gx = [x for c, x in zip(cids, xs) if c % 2 == parity]
                gy = [y for c, y in zip(cids, ys) if c % 2 == parity]
                if not gx:
                    return 0
                py = gy
                if lim is not None:
                    clipped = sum(1 for y in gy if y > lim)
                    py = [min(y, lim) for y in gy]
                ax.scatter(gx, py, s=14, color=pt_color, alpha=0.65,
                           edgecolors="none", label=label)
                if trend_var.get() and max_r >= R_GATE and len(gy) >= 5:
                    order = np.argsort(gx)
                    xs_s = np.asarray(gx, dtype=float)[order]
                    ys_s = np.asarray(gy, dtype=float)[order]
                    k = min(15, len(ys_s))
                    if k % 2 == 0:
                        k -= 1
                    if k >= 3:
                        tr_raw = medfilt(ys_s, k)
                        tr = (np.minimum(tr_raw, lim)
                              if lim is not None else tr_raw)
                        ax.plot(xs_s, tr, color=tr_color, linewidth=2,
                                label=f"{label} trend")
                        # Mark and label the trend's maximum (true value,
                        # even if the displayed line is clipped)
                        i_max = int(np.argmax(tr_raw))
                        ax.plot(xs_s[i_max], tr[i_max], marker="o",
                                color=tr_color, markersize=6)
                        ax.annotate(
                            f"max {tr_raw[i_max]:.3g} @ {int(xs_s[i_max])}",
                            xy=(xs_s[i_max], tr[i_max]),
                            xytext=(0, 8), textcoords="offset points",
                            ha="center", fontsize=8, fontweight="bold",
                            color=tr_color)
                return clipped

            n_clipped += _plot_group(1, "#7db6e8", "#1f77b4", "Odd ch")
            n_clipped += _plot_group(0, "#f0a35e", "#d62728", "Even ch")

            if lim is not None:
                if mode == "|r|" and not kernel:
                    ax.set_ylim(0, min(1.0, lim))
                else:
                    ax.set_ylim(0, lim)
            elif mode == "|r|" and not kernel:
                ax.set_ylim(0, 1)
            ax.axvline(agg_seq if phys else agg_cid, color="red",
                       linestyle="--", linewidth=1, label="Aggressor")
            ax.set_xlabel("Physical position" if phys else "Channel ID")
            title = "Kernel ±1" if kernel else "Correlation"
            cm_txt = ""
            extra = []
            if n_clipped:
                extra.append(f"{n_clipped} clipped")
            if excluded:
                extra.append(f"{len(excluded)} excluded")
            g = _rows(gated=True)
            if g:
                best = max(g, key=lambda z: z[4])
                extra.append(f"max ratio {best[4]:.2f}% @ ch {best[0]} "
                             f"(|r|={abs(best[2]):.2f})")
            extra_txt = f"  ({', '.join(extra)})" if extra else ""
            ax.set_title(
                f"{title} crosstalk vs Ch {agg_cid}{cm_txt}{extra_txt}",
                fontsize=10)
            ax.legend(loc="upper right", fontsize=8)
            ax.grid(True, alpha=0.3)
            canvas.draw_idle()

        canvas.get_tk_widget().pack(fill="both", expand=True)
        _refresh()

    # ── Session stash ────────────────────────────────────────────────────
    # After every scan the results are saved to disk together with a
    # fingerprint of the detection code and the parameters used. "Resume
    # Last Session" restores them instantly — but only if the data files
    # and the step-finding routine are unchanged, so stale results can
    # never masquerade as current ones.

    def _stash_path(self) -> str:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(app_dir, "step_finder_stash.json")

    @staticmethod
    def _detector_fingerprint() -> str:
        """Hash the source of the detection routine (+ helpers)."""
        src = "".join(inspect.getsource(f) for f in
                      (find_step_noise, _bandpass, compute_bandpass_coefs))
        return hashlib.md5(src.encode()).hexdigest()

    def _save_stash(self, found: list):
        """Persist the last scan (best-effort)."""
        if not self._batch_files:
            return
        files = [[fp, os.path.getsize(fp)] for fp in self._batch_files
                 if os.path.exists(fp)]
        if not files:
            return
        data = {
            "app_version": __version__,
            "detector": self._detector_fingerprint(),
            "saved": datetime.now().isoformat(timespec="seconds"),
            "label": self._batch_label,
            "params": {
                "amp": self.amp_var.get(),
                "freq_lo": self.freq_lo_var.get(),
                "freq_hi": self.freq_hi_var.get(),
                "periodicity": self.per_var.get(),
                "gap": self.gap_var.get(),
                "envelope_mode": self.envelope_var.get(),
            },
            "files": files,
            "found": [[fp, cid, col, segs]
                      for fp, cid, col, segs in found],
        }
        try:
            with open(self._stash_path(), "w") as f:
                json.dump(data, f, default=float)
        except OSError:
            pass

    def _load_valid_stash(self) -> dict | None:
        """Load the stash; return None if data or detector changed."""
        try:
            with open(self._stash_path(), "r") as f:
                data = json.load(f)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if data.get("detector") != self._detector_fingerprint():
            return None
        for fp, size in data.get("files", []):
            if not os.path.exists(fp) or os.path.getsize(fp) != size:
                return None
        if not data.get("files"):
            return None
        return data

    def _restore_stash(self):
        if self._scanning:
            return
        data = self._load_valid_stash()
        if data is None:
            messagebox.showinfo(
                "Resume Last Session",
                "No resumable session found.\n\nThe stash is invalid when "
                "the data files moved/changed or the detection routine "
                "was modified — rescan to rebuild it.")
            return

        # Apply the stashed parameters so a rescan reproduces the results
        p = data.get("params", {})
        for key, var in (("amp", self.amp_var),
                         ("freq_lo", self.freq_lo_var),
                         ("freq_hi", self.freq_hi_var),
                         ("periodicity", self.per_var),
                         ("gap", self.gap_var),
                         ("envelope_mode", self.envelope_var)):
            if key in p:
                try:
                    var.set(p[key])
                except Exception:
                    pass

        self.filepath = None
        self.header = None
        self.data = None
        self.chan_map = []
        self._batch_files = [f[0] for f in data["files"]]
        self._batch_folder = os.path.dirname(self._batch_files[0])
        self._batch_label = data.get("label", "")

        found = [(fp, cid, col, segs)
                 for fp, cid, col, segs in data.get("found", [])]
        # report_paths=[] suppresses report regeneration on restore
        self._scan_done(found, report_paths=[], from_stash=True)
        label = self._batch_label or os.path.basename(self._batch_folder)
        self.file_lbl.config(
            text=f"{label}/  ({len(self._batch_files)} files, restored)")
        self._status(f"Session restored from {data.get('saved', '?')} — "
                     f"{len(found)} channel entr(ies). Parameters applied; "
                     f"Scan All Channels rescans the same files.")


    def _write_reports_now(self):
        """Regenerate per-folder reports from the current results.

        Useful after 'Xtalk All Events', since the scan-time reports were
        written before the crosstalk column existed.
        """
        if self._scanning or not self.results:
            self._status("Nothing to write — scan first.")
            return
        paths = []
        folders = sorted({os.path.dirname(f[0]) for f in self.results})
        for folder in folders:
            ff = [f for f in self.results if os.path.dirname(f[0]) == folder]
            files = [fp for fp in self._batch_files
                     if os.path.dirname(fp) == folder] or [f[0] for f in ff]
            rp = self._write_report(ff, folder, files)
            if rp:
                paths.append(rp)
        self._status(f"{len(paths)} report(s) rewritten to results/"
                     if paths else "Report write failed.")

    # ── Xtalk @Agg column ─────────────────────────────────────────────────

    def _start_xtalk_all(self):
        """Fill the 'Xtalk @Agg (%)' column for every row in the table."""
        if self._scanning:
            return
        items = list(self.tree.get_children())
        if not items:
            self._status("Nothing to compute — scan first.")
            return
        self._scanning = True
        self._start_busy_indicator()
        threading.Thread(target=self._xtalk_all_worker, args=(items,),
                         daemon=True).start()

    def _xtalk_all_worker(self, items):
        """Per event: trend value of the Ratio at the channel nearest the
        aggressor, using the aggressor's own parity (the stronger,
        physically meaningful path). Blank when not measurable."""
        total = len(items)
        for i, iid in enumerate(items):
            if self._stop_event.is_set():
                break
            try:
                vals = self.tree.item(iid)["values"]
                agg_cid = int(vals[1])
                start, end = float(vals[2]), float(vals[3])
                filepath = self.tree.item(iid)["tags"][0]
                txt = self._xtalk_at_aggressor(filepath, agg_cid, start, end)
                # keep it on the segment so .txt/.json reports can use it
                for fp, cid_, _col, segs in self.results:
                    if fp == filepath and cid_ == agg_cid:
                        for sg in segs:
                            if abs(sg["start_sec"] - start) < 0.05:
                                sg["xtalk_at_agg"] = txt
            except Exception:
                txt = "—"
            self.root.after(0, self._set_tree_value, iid, "xtalk", txt)
            self.root.after(0, self._update_progress, i + 1, total,
                            f"Xtalk ch {agg_cid}")
        self.root.after(0, self._xtalk_all_done)

    def _set_tree_value(self, iid, col, txt):
        try:
            self.tree.set(iid, col, txt)
        except tk.TclError:
            pass

    def _xtalk_all_done(self):
        self._stop_busy_indicator()
        self.prog_lbl.config(text="")
        self._status("Xtalk @Agg column complete.")

    def _xtalk_at_aggressor(self, filepath, agg_cid, start_sec, end_sec):
        """Return the trend Ratio nearest the aggressor as a string."""
        header, data, chan_map = self._load_tdd(filepath)
        fs = header.sample_rate
        half = min(10.0, end_sec - start_sec) / 2.0
        center = (start_sec + end_sec) / 2.0
        s0 = max(0, int((center - half) * fs))
        s1 = min(data.shape[0], int((center + half) * fs))
        if s1 - s0 < int(0.5 * fs):
            return "—"

        c2c = {cm["cid"]: cm["col"] for cm in chan_map}
        if agg_cid not in c2c:
            return "—"
        agg = data[s0:s1, c2c[agg_cid]].astype(np.float64)
        agg -= agg.mean()
        agg_n = float(np.linalg.norm(agg)) + 1e-20
        agg_E = float((agg ** 2).sum()) + 1e-20

        win_pp = data[s0:s1, :].max(axis=0) - data[s0:s1, :].min(axis=0)
        loud_thr = 0.25 * win_pp[c2c[agg_cid]]

        # same-parity victims only, co-aggressors dropped
        pts = []
        for cm in chan_map:
            cid, col = cm["cid"], cm["col"]
            if cid == agg_cid or cid % 2 != agg_cid % 2:
                continue
            if win_pp[col] > loud_thr:
                continue
            v = data[s0:s1, col].astype(np.float64)
            v -= v.mean()
            dot = float(agg @ v)
            r = abs(dot / (agg_n * (float(np.linalg.norm(v)) + 1e-20)))
            pts.append((cid, r, abs(dot / agg_E) * 100.0))

        if len(pts) < 7 or max(p[1] for p in pts) < R_GATE:
            return "—"          # not measurable

        pts.sort(key=lambda z: z[0])
        cids = np.array([p[0] for p in pts])
        trend = medfilt(np.array([p[2] for p in pts], dtype=float), 7)
        j = int(np.argmin(np.abs(cids - agg_cid)))
        return f"{trend[j]:.3f}"

    # ── Config persistence ───────────────────────────────────────────────

    def _config_path(self) -> str:
        """Return path to the user-settings JSON file (next to the app dir)."""
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(app_dir, "step_finder_config.json")

    _CONFIG_FIELDS = (
        ("amp", "amp_var"),
        ("freq_lo", "freq_lo_var"),
        ("freq_hi", "freq_hi_var"),
        ("periodicity", "per_var"),
        ("gap", "gap_var"),
        ("envelope_mode", "envelope_var"),
        ("full_file", "full_file_var"),
    )

    def _load_config(self):
        """Load saved settings and apply them to the toolbar Tk vars."""
        try:
            with open(self._config_path(), "r") as f:
                cfg = json.load(f)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        for key, attr in self._CONFIG_FIELDS:
            if key in cfg:
                try:
                    getattr(self, attr).set(cfg[key])
                except Exception:
                    pass

    def _save_config(self):
        """Persist the current toolbar settings to disk (best-effort)."""
        cfg = {}
        for key, attr in self._CONFIG_FIELDS:
            try:
                cfg[key] = getattr(self, attr).get()
            except Exception:
                continue
        try:
            with open(self._config_path(), "w") as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _short_name(filepath: str) -> str:
        """Truncate the part before the first '_' in the filename."""
        name = os.path.basename(filepath)
        return name.split("_", 1)[-1] if "_" in name else name

    @staticmethod
    def _datetime_code(filepath: str) -> str:
        """Extract the 14-digit datetime code from the TDD filename."""
        import re
        name = os.path.basename(filepath)
        m = re.search(r"\d{14}", name)
        return m.group(0) if m else name

    # ── File handling ─────────────────────────────────────────────────────

    def _load_tdd(self, fp: str):
        """Load a TDD file with an LRU byte-bounded cache.

        Cache hit: move to end (mark as most-recently-used) and return.
        Cache miss: parse + load from disk, then evict oldest entries until
        the new file fits under self._MAX_CACHE_BYTES.
        """
        # Cache hit — touch to mark as MRU
        if fp in self._file_cache:
            entry = self._file_cache.pop(fp)
            self._file_cache[fp] = entry
            return entry

        # Cache miss — load from disk
        header = parse_header(fp)
        data, _ = load_data(fp, header)
        chan_map = descramble_channels(header.channel_ids)
        nbytes = int(getattr(data, "nbytes", 0))

        # If even one file is larger than the limit, still cache it alone
        # (we evict everything else). This keeps selection-plot working.
        while (self._cache_bytes + nbytes > self._MAX_CACHE_BYTES
               and self._file_cache):
            old_fp, old_entry = next(iter(self._file_cache.items()))
            del self._file_cache[old_fp]
            old_bytes = int(getattr(old_entry[1], "nbytes", 0))
            self._cache_bytes = max(0, self._cache_bytes - old_bytes)

        self._file_cache[fp] = (header, data, chan_map)
        self._cache_bytes += nbytes
        return header, data, chan_map

    def _clear_cache(self):
        """Drop all cached files and reset the byte counter."""
        self._file_cache.clear()
        self._cache_bytes = 0

    def _open_file(self):
        fp = filedialog.askopenfilename(
            title="Open TDD baseline file",
            filetypes=[("TDD files", "*.tdd"), ("All files", "*.*")],
        )
        if not fp:
            return
        try:
            self._clear_cache()
            header, data, chan_map = self._load_tdd(fp)
            self.header = header
            self.data = data
            self.chan_map = chan_map
            self.filepath = fp
            dur = data.shape[0] / header.sample_rate
            self.file_lbl.config(
                text=f"{self._short_name(fp)}  ({header.channel_count} ch, "
                     f"{header.sample_rate} Hz, {dur:.0f} s)"
            )
            self.scan_btn.config(state="normal")
            self._status(f"Loaded {os.path.basename(fp)} — {header.channel_count} "
                         f"channels, {dur:.0f}s.  Press Scan.")
            self.tree.delete(*self.tree.get_children())
            self.results.clear()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to open file:\n{exc}")

    def _pick_folder(self, parent=None) -> str | None:
        """Show a folder picker that previews Baseline TDD files."""
        parent = parent or self.root
        dlg = tk.Toplevel(parent)
        dlg.title("Select Folder")
        dlg.geometry("560x340")
        dlg.transient(parent)
        dlg.grab_set()

        result: list[str | None] = [None]  # mutable container for result

        # Folder path row
        path_frame = ttk.Frame(dlg, padding=(10, 10, 10, 4))
        path_frame.pack(fill="x")
        ttk.Label(path_frame, text="Folder:").pack(side="left")
        path_var = tk.StringVar()
        path_entry = ttk.Entry(path_frame, textvariable=path_var, state="readonly")
        path_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))

        # File preview list
        ttk.Label(dlg, text="Baseline TDD files in folder:",
                  padding=(10, 4)).pack(anchor="w")
        preview_frame = ttk.Frame(dlg, padding=(10, 0, 10, 0))
        preview_frame.pack(fill="both", expand=True)
        preview_list = tk.Listbox(preview_frame)
        psb = ttk.Scrollbar(preview_frame, orient="vertical",
                            command=preview_list.yview)
        preview_list.configure(yscrollcommand=psb.set)
        preview_list.pack(side="left", fill="both", expand=True)
        psb.pack(side="right", fill="y")

        def _update_preview(folder):
            path_var.set(folder)
            preview_list.delete(0, "end")
            for fp in sorted(glob.glob(os.path.join(folder, "*Baseline*.tdd"))):
                preview_list.insert("end", os.path.basename(fp))

        def _browse():
            pick = filedialog.askopenfilename(
                title="Select any TDD in the folder to scan",
                filetypes=[("Baseline TDD", "*Baseline*.tdd"),
                           ("All TDD files", "*.tdd")],
                parent=dlg)
            if pick:
                _update_preview(os.path.dirname(pick))

        ttk.Button(path_frame, text="Browse…", command=_browse).pack(side="left")

        def _ok():
            folder = path_var.get()
            if not folder:
                messagebox.showinfo("No folder", "Browse to a folder first.",
                                    parent=dlg)
                return
            if preview_list.size() == 0:
                messagebox.showinfo("No files",
                                    "No *Baseline*.tdd files in this folder.",
                                    parent=dlg)
                return
            result[0] = folder
            dlg.destroy()

        btn_frame = ttk.Frame(dlg, padding=10)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="OK", command=_ok).pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 6))

        dlg.wait_window()
        return result[0]

    def _scan_folder(self):
        """Pick a folder — scans all *Baseline*.tdd files in it."""
        folder = self._pick_folder()
        if not folder:
            return
        tdd_files = sorted(glob.glob(os.path.join(folder, "*Baseline*.tdd")))
        if not tdd_files:
            return
        self._launch_batch_scan(tdd_files)

    def _scan_files(self):
        """Pick individual TDD files to scan."""
        files = filedialog.askopenfilenames(
            title="Select TDD files to scan",
            filetypes=[("Baseline TDD files", "*Baseline*.tdd"),
                       ("All TDD files", "*.tdd")],
        )
        if not files:
            return
        self._launch_batch_scan(sorted(files))

    def _scan_multi_folders(self):
        """Show a dialog where the user can add/remove folders, then scan."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Select Folders to Scan")
        dlg.geometry("620x400")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="Folders to scan (*Baseline*.tdd files):").pack(
            anchor="w", padx=10, pady=(10, 4))

        list_frame = ttk.Frame(dlg)
        list_frame.pack(fill="both", expand=True, padx=10)

        listbox = tk.Listbox(list_frame, selectmode="extended")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=sb.set)
        listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Track folder paths (listbox shows basename + file count)
        folder_paths: list[str] = []

        def _add_folder():
            folder = self._pick_folder(parent=dlg)
            if not folder:
                return
            if folder in folder_paths:
                messagebox.showinfo("Duplicate",
                                    f"Folder already in list:\n{folder}",
                                    parent=dlg)
                return
            found = sorted(glob.glob(os.path.join(folder, "*Baseline*.tdd")))
            if not found:
                messagebox.showinfo("No files",
                                    f"No *Baseline*.tdd files in:\n{folder}",
                                    parent=dlg)
                return
            folder_paths.append(folder)
            listbox.insert("end", f"{folder}  ({len(found)} files)")

        def _remove_selected():
            for idx in reversed(listbox.curselection()):
                listbox.delete(idx)
                folder_paths.pop(idx)

        def _scan():
            if not folder_paths:
                messagebox.showinfo("No folders",
                                    "Add at least one folder to scan.",
                                    parent=dlg)
                return
            all_files = []
            folder_names = []
            for fp in folder_paths:
                found = sorted(glob.glob(os.path.join(fp, "*Baseline*.tdd")))
                all_files.extend(found)
                folder_names.append(os.path.basename(fp))
            dlg.destroy()
            if all_files:
                self._launch_batch_scan(all_files,
                                        folder_label=", ".join(folder_names))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=10, pady=8)
        ttk.Button(btn_frame, text="Add Folder…", command=_add_folder).pack(
            side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="Remove Selected", command=_remove_selected).pack(
            side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="Scan", command=_scan).pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 6))

    # ── Scan All Runs (recursive data-root scan) ──────────────────────────

    def _scan_data_root(self):
        """Pick a data root and discover every folder with *Baseline*.tdd files."""
        root = filedialog.askdirectory(
            title="Select data root to scan recursively")
        if not root:
            return

        self._status(f"Scanning tree under {root}…")
        self.root.update_idletasks()
        runs = self._discover_runs(root)

        if not runs:
            self._status("Ready")
            messagebox.showinfo(
                "No runs found",
                f"No *Baseline*.tdd files found under:\n{root}")
            return

        self._status(f"Found {len(runs)} run(s) under {root}")
        self._show_runs_preview(root, runs)

    @staticmethod
    def _discover_runs(root: str) -> dict:
        """Walk root recursively; return {run_folder: [tdd_paths...]}."""
        runs: dict[str, list[str]] = {}
        for dirpath, _dirnames, filenames in os.walk(
                root, followlinks=False, onerror=lambda _e: None):
            matches = sorted(
                os.path.join(dirpath, f) for f in filenames
                if fnmatch.fnmatch(f, "*Baseline*.tdd")
            )
            if matches:
                runs[dirpath] = matches
        return runs

    def _show_runs_preview(self, root: str, runs: dict):
        """Show discovered runs in a Treeview; user picks which to scan."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Discovered Runs")
        dlg.geometry("820x480")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg,
                  text=f"Discovered {len(runs)} run(s) under: {root}",
                  padding=(10, 10, 10, 4)).pack(anchor="w")

        tv_frame = ttk.Frame(dlg, padding=(10, 0, 10, 0))
        tv_frame.pack(fill="both", expand=True)

        tree = ttk.Treeview(tv_frame, columns=("run", "count"),
                            show="headings", selectmode="extended")
        tree.heading("run", text="Run folder")
        tree.heading("count", text="TDD files")
        tree.column("run", width=640, anchor="w")
        tree.column("count", width=80, anchor="center", stretch=False)

        sb = ttk.Scrollbar(tv_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Populate; iid = full run-folder path, values = (relpath, count)
        for dirpath in sorted(runs):
            rel = os.path.relpath(dirpath, root)
            if rel == ".":
                rel = os.path.basename(dirpath.rstrip(os.sep)) or dirpath
            tree.insert("", "end", iid=dirpath,
                        values=(rel, len(runs[dirpath])))

        def _remove_selected():
            for iid in tree.selection():
                tree.delete(iid)

        def _scan():
            remaining = tree.get_children()
            if not remaining:
                messagebox.showinfo("No runs",
                                    "No runs left to scan.", parent=dlg)
                return
            all_files: list[str] = []
            for iid in remaining:
                all_files.extend(runs[iid])
            dlg.destroy()
            label = (f"{os.path.basename(root.rstrip(os.sep)) or root} "
                     f"({len(remaining)} runs)")
            self._launch_batch_scan(all_files, folder_label=label)

        btn_frame = ttk.Frame(dlg, padding=10)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Remove Selected",
                   command=_remove_selected).pack(side="left")
        ttk.Button(btn_frame, text="Scan", command=_scan).pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(0, 6))

    def _launch_batch_scan(self, tdd_files: list, folder_label: str | None = None):
        self._clear_cache()
        self.filepath = None
        self.header = None
        self.data = None
        self.chan_map = []
        self._batch_folder = os.path.dirname(tdd_files[0])
        self._batch_files = tdd_files
        if folder_label is None:
            folder_label = os.path.basename(self._batch_folder)
        self._batch_label = folder_label
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.scan_btn.config(state="disabled")
        self.file_lbl.config(
            text=f"{folder_label}/  ({len(tdd_files)} Baseline files)")
        self._status(f"Scanning {len(tdd_files)} file(s) in {folder_label}…")
        self._start_folder_scan(tdd_files)

    # ── Scanning ──────────────────────────────────────────────────────────

    def _start_scan(self):
        if self._scanning:
            return
        # Single-file mode: a TDD was opened via "Open TDD…"
        if self.data is not None:
            self._scanning = True
            self._batch_folder = os.path.dirname(self.filepath)
            self._batch_files = [self.filepath]
            self.scan_btn.config(state="disabled")
            self.tree.delete(*self.tree.get_children())
            self.results.clear()
            self.progress["value"] = 0
            self.progress["maximum"] = len(self.chan_map)
            self._start_busy_indicator()
            threading.Thread(
                target=self._scan_worker,
                args=(self.filepath, self.header, self.data, self.chan_map),
                daemon=True,
            ).start()
            return
        # Batch-rescan mode: re-run the most recent batch with current params
        if self._batch_files:
            self._launch_batch_scan(self._batch_files,
                                    folder_label=self._batch_label or None)

    def _start_folder_scan(self, tdd_files: list):
        if self._scanning:
            return
        self._scanning = True
        self.scan_btn.config(state="disabled")
        self.folder_btn.config(state="disabled")
        self.progress["value"] = 0
        self._start_busy_indicator()
        threading.Thread(target=self._folder_scan_worker, args=(tdd_files,),
                         daemon=True).start()

    # ── Analyzing indicator ───────────────────────────────────────────────

    def _start_busy_indicator(self):
        self._busy_dots = 0
        self._stop_event.clear()
        self.stop_btn.config(state="normal")
        self._tick_busy_indicator()

    def _stop_scan(self):
        """Request the current scan to stop at the next safe checkpoint."""
        if not self._scanning:
            return
        self._stop_event.set()
        self.stop_btn.config(state="disabled")
        self._status("Stop requested — finishing current channels…")

    def _tick_busy_indicator(self):
        if not self._scanning:
            self.busy_lbl.config(text="")
            self._busy_after_id = None
            return
        self._busy_dots = (self._busy_dots + 1) % 4
        dots = "." * self._busy_dots
        self.busy_lbl.config(text=f"● Analyzing{dots}")
        self._busy_after_id = self.root.after(400, self._tick_busy_indicator)

    def _stop_busy_indicator(self):
        self._scanning = False
        self.stop_btn.config(state="disabled")
        if self._busy_after_id is not None:
            self.root.after_cancel(self._busy_after_id)
            self._busy_after_id = None
        self.busy_lbl.config(text="")

    def _scan_worker(self, filepath, header, data, chan_map):
        amp = self.amp_var.get()
        flo = self.freq_lo_var.get()
        fhi = self.freq_hi_var.get()
        per = self.per_var.get()
        gap = self.gap_var.get()
        envelope = self.envelope_var.get()
        total_ch = len(chan_map)
        found = []

        # Pre-compute filter coefficients once (shared read-only across threads)
        coefs = compute_bandpass_coefs(header.sample_rate, flo, fhi)

        def _scan_one(cm):
            cid_, col_ = cm["cid"], cm["col"]
            ch_data = data[:, col_].astype(np.float64)
            segs = find_step_noise(
                ch_data, header.sample_rate,
                freq_lo=flo, freq_hi=fhi,
                min_amplitude_mv=amp, min_periodicity=per,
                max_gap_sec=gap, filter_coefs=coefs,
                envelope_mode=envelope,
            )
            return cid_, col_, segs

        max_workers = min(os.cpu_count() or 1, 8)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_scan_one, cm) for cm in chan_map]
            done = 0
            for fut in as_completed(futures):
                if self._stop_event.is_set():
                    for f in futures:
                        f.cancel()
                    break
                cid, col, segments = fut.result()
                done += 1
                if segments:
                    found.append((filepath, cid, col, segments))
                self.root.after(0, self._update_progress, done, total_ch,
                                f"Ch {cid}")

        self.root.after(0, self._scan_done, found)

    def _folder_scan_worker(self, tdd_files: list):
        amp = self.amp_var.get()
        flo = self.freq_lo_var.get()
        fhi = self.freq_hi_var.get()
        per = self.per_var.get()
        gap = self.gap_var.get()
        envelope = self.envelope_var.get()
        n_files = len(tdd_files)
        all_found = []
        file_start_times: dict = {}   # filepath -> data_start_time (sec since 1904)
        report_paths = []
        max_workers = min(os.cpu_count() or 1, 8)

        # Group files by source folder, preserving scan order
        folders_ordered = []
        folder_to_files = {}
        for fp in tdd_files:
            d = os.path.dirname(fp)
            if d not in folder_to_files:
                folder_to_files[d] = []
                folders_ordered.append(d)
            folder_to_files[d].append(fp)

        folder_idx = 0
        files_done_in_folder = 0

        for fi, fp in enumerate(tdd_files):
            if self._stop_event.is_set():
                break
            fname = self._short_name(fp)
            try:
                header, data, chan_map = self._load_tdd(fp)
            except Exception:
                self.root.after(0, self._update_progress, fi + 1, n_files,
                                f"SKIP {fname}")
                chan_map = None

            if chan_map is not None:
                total_ch = len(chan_map)
                # Remember when this file started (wall-clock) for cross-file merging
                file_start_times[fp] = header.data_start_time
                # Pre-compute filter coefficients once per file (shared across threads)
                coefs = compute_bandpass_coefs(header.sample_rate, flo, fhi)

                def _scan_one(cm, _fp=fp, _data=data, _fs=header.sample_rate,
                              _coefs=coefs):
                    cid_, col_ = cm["cid"], cm["col"]
                    ch_data = _data[:, col_].astype(np.float64)
                    segs = find_step_noise(
                        ch_data, _fs,
                        freq_lo=flo, freq_hi=fhi,
                        min_amplitude_mv=amp, min_periodicity=per,
                        max_gap_sec=gap, filter_coefs=_coefs,
                        envelope_mode=envelope,
                    )
                    return _fp, cid_, col_, segs

                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futures = [ex.submit(_scan_one, cm) for cm in chan_map]
                    ci_done = 0
                    for fut in as_completed(futures):
                        if self._stop_event.is_set():
                            for f in futures:
                                f.cancel()
                            break
                        file_fp, cid, col, segments = fut.result()
                        ci_done += 1
                        if segments:
                            all_found.append((file_fp, cid, col, segments))
                        self.root.after(
                            0, self._update_progress,
                            fi * total_ch + ci_done,
                            n_files * total_ch,
                            f"{fname}  Ch {cid}",
                        )

                # Memory is bounded by the 8 GB LRU cache in _load_tdd —
                # older files are evicted automatically as new ones load.
                # If the user later selects a row whose file was evicted,
                # _load_tdd will transparently reload it from disk.

            # Write this folder's report once all its files have been scanned
            cur_folder = folders_ordered[folder_idx]
            files_done_in_folder += 1
            if files_done_in_folder == len(folder_to_files[cur_folder]):
                folder_files = folder_to_files[cur_folder]
                folder_found = [x for x in all_found
                                if os.path.dirname(x[0]) == cur_folder]
                # Merge events across files in this folder (same channel, gap < max_gap_sec wall-clock)
                folder_found = self._merge_across_files(
                    folder_found, file_start_times, gap)
                # Replace per-folder slice in all_found with the merged version
                all_found = ([x for x in all_found
                              if os.path.dirname(x[0]) != cur_folder]
                             + folder_found)
                if folder_found:
                    rp = self._write_report(folder_found, cur_folder, folder_files)
                    if rp:
                        report_paths.append(rp)
                        self.root.after(0, self._status,
                                        f"Report written: {os.path.basename(rp)}")
                folder_idx += 1
                files_done_in_folder = 0

        self.root.after(0, self._scan_done, all_found, report_paths)

    @staticmethod
    def _merge_across_files(folder_found: list, file_start_times: dict,
                            max_gap_sec: float) -> list:
        """Merge segments across files for the same channel within a folder.

        Two segments belong to the same event if their wall-clock gap is
        within max_gap_sec. The merged event is stored against the first
        contributing file with start_sec/end_sec measured from that
        file's start. Time may exceed the first file's duration when an
        event spans multiple files.
        """
        # Group entries by channel
        by_channel: dict = {}
        for filepath, cid, col, segments in folder_found:
            by_channel.setdefault(cid, []).append((filepath, col, segments))

        merged_results = []
        for cid, items in by_channel.items():
            # Flatten to per-segment records with wall-clock times
            recs = []
            for filepath, col, segments in items:
                t0 = file_start_times.get(filepath, 0.0)
                for seg in segments:
                    recs.append({
                        "filepath": filepath,
                        "col": col,
                        "wall_start": t0 + seg["start_sec"],
                        "wall_end": t0 + seg["end_sec"],
                        "seg": seg,
                    })
            recs.sort(key=lambda r: r["wall_start"])

            # Walk in order, merging within max_gap_sec
            i = 0
            while i < len(recs):
                grp = [recs[i]]
                j = i + 1
                while j < len(recs) and \
                        recs[j]["wall_start"] - grp[-1]["wall_end"] <= max_gap_sec:
                    grp.append(recs[j])
                    j += 1

                first = grp[0]
                first_t0 = file_start_times.get(first["filepath"], 0.0)
                wall_start = grp[0]["wall_start"]
                wall_end = max(r["wall_end"] for r in grp)
                amps = [r["seg"]["amplitude_mv"] for r in grp]
                pers = [r["seg"]["periodicity"] for r in grp]
                freqs = [r["seg"]["frequency_hz"] for r in grp]

                # Pre-baseline: from first segment of first file
                pre_bl = first["seg"]["baseline_mv"]
                # Post-baseline: from last segment that has one
                post_bl = None
                for r in reversed(grp):
                    if r["seg"].get("post_baseline_mv") is not None:
                        post_bl = r["seg"]["post_baseline_mv"]
                        break

                merged_seg = {
                    "start_sec": wall_start - first_t0,
                    "end_sec": wall_end - first_t0,
                    "frequency_hz": round(sum(freqs) / len(freqs), 1),
                    "amplitude_mv": round(max(amps), 1),
                    "baseline_mv": pre_bl,
                    "post_baseline_mv": post_bl,
                    "periodicity": round(max(pers), 3),
                    "n_files": len({r["filepath"] for r in grp}),
                }
                merged_results.append(
                    (first["filepath"], cid, first["col"], [merged_seg]))
                i = j

        return merged_results

    def _update_progress(self, done: int, total: int, label: str):
        self.progress["maximum"] = total
        self.progress["value"] = done
        self.prog_lbl.config(text=f"{label}  ({done}/{total})")

    def _scan_done(self, found: list, report_paths: list | None = None,
                   from_stash: bool = False):
        self._stop_busy_indicator()
        self.scan_btn.config(
            state="normal" if (self.data is not None or self._batch_files)
            else "disabled"
        )
        self.folder_btn.config(state="normal")
        self.results = found
        self.prog_lbl.config(text="")

        # Populate table
        for filepath, cid, col, segments in found:
            fname = self._short_name(filepath)
            for seg in segments:
                post_bl = (f"{seg['post_baseline_mv']}"
                           if seg["post_baseline_mv"] is not None else "—")
                pre_bl = (f"{seg['baseline_mv']}"
                          if seg["baseline_mv"] is not None else "—")
                self.tree.insert(
                    "", "end",
                    values=(
                        fname,
                        cid,
                        f"{seg['start_sec']:.1f}",
                        f"{seg['end_sec']:.1f}",
                        seg["frequency_hz"],
                        seg["amplitude_mv"],
                        pre_bl,
                        post_bl,
                        seg["periodicity"],
                        seg.get("xtalk_at_agg", "—"),
                    ),
                    tags=(filepath, str(col)),
                )

        n_files = len({f[0] for f in found})
        n_ch = len({(f[0], f[1]) for f in found})
        n_seg = sum(len(s) for _, _, _, s in found)

        # Reports were written incrementally by the folder scan worker.
        # For single-file scans (no incremental reports), write one now.
        if report_paths is None:
            report_paths = []
            if self._batch_folder and found:
                folders = sorted({os.path.dirname(f[0]) for f in found})
                for folder in folders:
                    folder_found = [f for f in found
                                    if os.path.dirname(f[0]) == folder]
                    folder_files = [fp for fp in self._batch_files
                                    if os.path.dirname(fp) == folder]
                    rp = self._write_report(folder_found, folder, folder_files)
                    if rp:
                        report_paths.append(rp)

        msg = f"Scan complete — {n_seg} segment(s) across {n_ch} channel(s) in {n_files} file(s)."
        if report_paths:
            msg += f"  {len(report_paths)} report(s) saved to results/"
        self._status(msg)

        # Stash this session so "Resume Last Session" can restore it.
        if not from_stash:
            self._save_stash(found)

    # ── Report generation ─────────────────────────────────────────────────

    @staticmethod
    def _run_name(filepath: str) -> str:
        """Extract the run name from a TDD filename.

        For "HAK04-008B-02L64869w15-205B16a-OHMX205-UNO256_205_..._Baseline.tdd"
        returns "HAK04-008B-02L64869w15-205B16a" (first 4 dash-separated parts).
        """
        base = os.path.basename(filepath)
        if base.lower().endswith(".tdd"):
            base = base[:-4]
        parts = base.split("-")
        return "-".join(parts[:4]) if len(parts) >= 4 else base

    def _write_report(self, found: list, folder: str,
                      folder_files: list) -> str | None:
        """Write a text report for one folder's results."""
        # Derive run name from a TDD filename in this folder
        source_files = folder_files or [f[0] for f in found]
        run_name = (self._run_name(source_files[0])
                    if source_files else os.path.basename(folder))
        safe_name = run_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        # Save to <app_dir>/results/
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        results_dir = os.path.join(app_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.txt"
        report_path = os.path.join(results_dir, filename)

        try:
            with open(report_path, "w") as f:
                # Header
                f.write("=" * 72 + "\n")
                f.write("  STEP NOISE FINDER — SCAN REPORT\n")
                f.write("=" * 72 + "\n\n")
                f.write(f"Date:       {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Folder:     {folder}\n")
                f.write(f"Files:      {len(folder_files)}\n")
                for fp in folder_files:
                    f.write(f"            - {os.path.basename(fp)}\n")
                f.write(f"\nParameters:\n")
                f.write(f"  Amplitude  >= {self.amp_var.get()} mV\n")
                f.write(f"  Frequency     {self.freq_lo_var.get()} - {self.freq_hi_var.get()} Hz\n")
                f.write(f"  Periodicity >= {self.per_var.get()}\n")
                f.write(f"  Gap merge  <= {self.gap_var.get()} s\n")

                # Summary
                n_files = len({f[0] for f in found})
                n_ch = len({(f[0], f[1]) for f in found})
                n_seg = sum(len(s) for _, _, _, s in found)
                f.write(f"\n{'=' * 72}\n")
                f.write(f"  SUMMARY\n")
                f.write(f"{'=' * 72}\n\n")
                f.write(f"  Channels with step noise: {n_ch}\n")
                f.write(f"  Total segments detected:  {n_seg}\n")
                f.write(f"  Files with detections:    {n_files}\n")

                # Results table
                f.write(f"\n{'=' * 72}\n")
                f.write(f"  DETECTIONS\n")
                f.write(f"{'=' * 72}\n\n")

                hdr = (f"{'File':<16} {'Ch':>4} {'Start':>8} {'End':>8} "
                       f"{'Freq':>6} {'Amp':>8} {'PreBL':>8} {'PostBL':>8} {'Period':>7}\n")
                f.write(hdr)
                f.write("-" * len(hdr.rstrip()) + "\n")

                for filepath, cid, col, segments in found:
                    fname = self._datetime_code(filepath)
                    for seg in segments:
                        post_bl = (f"{seg['post_baseline_mv']:>8.1f}"
                                   if seg["post_baseline_mv"] is not None else "     N/A")
                        pre_bl = (f"{seg['baseline_mv']:>8.1f}"
                                  if seg["baseline_mv"] is not None else "     N/A")
                        f.write(
                            f"{fname:<16} {cid:>4} {seg['start_sec']:>8.1f} "
                            f"{seg['end_sec']:>8.1f} {seg['frequency_hz']:>6.1f} "
                            f"{seg['amplitude_mv']:>8.1f} {pre_bl} "
                            f"{post_bl} {seg['periodicity']:>7.3f}\n"
                        )

                f.write(f"\n{'=' * 72}\n")
                f.write(f"  End of report\n")
                f.write(f"{'=' * 72}\n")
        except Exception:
            return None

        # Also write a Sherlock-compatible JSON sibling (best-effort)
        try:
            json_path = report_path[:-4] + ".json"
            self._write_run_results_json(found, source_files, json_path)
        except Exception:
            pass  # JSON is optional; .txt is the primary artifact

        return report_path

    # ── Sherlock-compatible JSON output ────────────────────────────────────

    def _write_run_results_json(self, found: list, source_files: list,
                                json_path: str) -> None:
        """Build and write sherlock.results.RunResults JSON for this folder."""
        from sherlock.results import RunResults, AggressorBlockResult
        from datetime import timedelta

        TSTMP_EPOCH = datetime(1904, 1, 1)

        # Pull run-level metadata from the earliest TDD file (by data_start_time)
        headers = []
        for fp in source_files:
            try:
                h = parse_header(fp)
                headers.append((h, fp))
            except Exception:
                continue
        if not headers:
            return
        headers.sort(key=lambda hf: hf[0].data_start_time)
        first_h, first_fp = headers[0]

        try:
            wafer = int(first_h.detector_wafer_id)
        except (TypeError, ValueError):
            wafer = 0

        results = RunResults(
            run_id        = first_h.run_id or self._run_name(first_fp),
            wafer_id      = wafer,
            die_number    = first_h.detector_die_number,
            lot_number    = first_h.detector_lot_number,
            instrument_id = first_h.system_id,
        )

        # Cache headers by filepath for the event-time conversion below
        header_by_fp = {fp: h for h, fp in headers}

        # Build aggressor entries: one per (file, channel) with segments
        for filepath, cid, _col, segments in found:
            if not segments:
                continue
            h = header_by_fp.get(filepath)
            if h is None:
                try:
                    h = parse_header(filepath)
                except Exception:
                    continue

            first_seg = segments[0]
            # Wall-clock ISO timestamp of the first event
            event_dt = TSTMP_EPOCH + timedelta(
                seconds=h.data_start_time + first_seg["start_sec"])
            block_key = event_dt.isoformat()

            # Amplitude-weighted mean dominant frequency
            total_amp = sum(s["amplitude_mv"] for s in segments)
            if total_amp > 0:
                dom_freq = sum(s["frequency_hz"] * s["amplitude_mv"]
                               for s in segments) / total_amp
            else:
                dom_freq = first_seg["frequency_hz"]

            # Sum of durations across segments
            total_time = sum(s["end_sec"] - s["start_sec"] for s in segments)

            # Peak-to-peak amplitude — worst segment
            amplitude = max(s["amplitude_mv"] for s in segments)

            agg = AggressorBlockResult(
                total_time_s     = round(total_time, 3),
                mean_baseline    = first_seg["baseline_mv"],   # pre-event baseline
                amplitude        = amplitude,
                dominant_freq_hz = round(dom_freq, 2),
            )
            results.aggressors.setdefault(cid, {})[block_key] = agg

        results.to_json(json_path)

        # Sherlock's AggressorBlockResult has a fixed schema, so add the
        # crosstalk ratio as an extra key afterwards (additive - readers
        # that expect only the Sherlock fields are unaffected).
        xt_by_cid = {}
        for _fp, cid, _col, segments in found:
            for sg in segments:
                v = sg.get("xtalk_at_agg")
                if v not in (None, "-", "—"):
                    try:
                        xt_by_cid[cid] = float(v)
                    except (TypeError, ValueError):
                        pass
                    break
        if xt_by_cid:
            try:
                with open(json_path, "r") as jf:
                    doc = json.load(jf)
                for cid, val in xt_by_cid.items():
                    blocks = doc.get("aggressors", {}).get(str(cid))
                    if blocks:
                        for blk in blocks.values():
                            blk["xtalk_at_agg_pct"] = val
                with open(json_path, "w") as jf:
                    json.dump(doc, jf, indent=2)
            except (OSError, json.JSONDecodeError):
                pass

    # ── Table sorting ─────────────────────────────────────────────────────

    def _sort_tree(self, col: str):
        """Sort the results Treeview by a column; toggle direction on re-click."""
        reverse = self._sort_state.get(col, False)
        items = [(self.tree.set(iid, col), iid)
                 for iid in self.tree.get_children("")]

        def _sort_key(pair):
            v = pair[0]
            # Try numeric first (handles int and float), fall back to string
            try:
                return (0, float(v))
            except (ValueError, TypeError):
                return (1, str(v).lower())

        items.sort(key=_sort_key, reverse=reverse)

        for new_idx, (_val, iid) in enumerate(items):
            self.tree.move(iid, "", new_idx)

        # Flip direction for next click; update header arrow indicator
        self._sort_state[col] = not reverse
        arrow = " ▼" if reverse else " ▲"
        for c, label in self._col_labels.items():
            self.tree.heading(c, text=label + (arrow if c == col else ""))

    # ── Plot on selection ─────────────────────────────────────────────────

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0])
        vals = item["values"]
        fname = vals[0]
        cid = int(vals[1])
        start_sec = float(vals[2])
        end_sec = float(vals[3])
        pre_bl_str = str(vals[6])
        pre_bl_mv = float(pre_bl_str) if pre_bl_str != "—" else None
        post_bl_str = vals[7]
        post_bl_mv = float(post_bl_str) if post_bl_str != "—" else None
        tags = item["tags"]
        filepath = tags[0]
        col = int(tags[1])

        # Load data from cache (or reload if evicted)
        try:
            header, data, _ = self._load_tdd(filepath)
        except Exception as exc:
            self._status(f"Cannot load {fname}: {exc}")
            return

        fs = header.sample_rate
        # Event window (used for filtered plot and, by default, raw plot)
        ev_s0 = max(0, int(start_sec * fs) - int(5 * fs))   # 5 s context
        ev_s1 = min(data.shape[0], int(end_sec * fs) + int(5 * fs))

        # Raw plot range: full file when checkbox is on, else event window
        if self.full_file_var.get():
            raw_s0, raw_s1 = 0, data.shape[0]
        else:
            raw_s0, raw_s1 = ev_s0, ev_s1

        raw_full_uv = data[raw_s0:raw_s1, col].astype(np.float64)
        t_raw = np.arange(raw_s0, raw_s1) / fs

        # Filtered always works on the event window (for speed + focus)
        filt_src_uv = data[ev_s0:ev_s1, col].astype(np.float64)
        t_filt = np.arange(ev_s0, ev_s1) / fs
        filt_uv = _bandpass(
            filt_src_uv, fs,
            max(0.5, self.freq_lo_var.get() - 1),
            min(fs / 2 - 1, self.freq_hi_var.get() + 5),
        )

        raw_mv = raw_full_uv / 1000
        filt_mv = filt_uv / 1000

        # Raw plot
        self.ax_raw.clear()
        self.ax_raw.plot(t_raw, raw_mv, linewidth=0.5, color="#1f77b4")
        self.ax_raw.axvspan(start_sec, end_sec, alpha=0.15, color="red",
                            label="Step noise")
        if pre_bl_mv is not None:
            self.ax_raw.axhline(pre_bl_mv, color="#2ca02c", linewidth=1,
                                linestyle="--",
                                label=f"Pre BL {pre_bl_mv:.1f} mV")
        if post_bl_mv is not None:
            self.ax_raw.axhline(post_bl_mv, color="#ff7f0e", linewidth=1,
                                linestyle="--", label=f"Post BL {post_bl_mv:.1f} mV")
        self.ax_raw.set_ylabel("mV")
        self.ax_raw.set_title(f"{fname} — Ch {cid} — Raw signal", fontsize=10)
        self.ax_raw.legend(loc="upper right", fontsize=8)

        # Filtered plot
        self.ax_filt.clear()
        self.ax_filt.plot(t_filt, filt_mv, linewidth=0.5, color="#d62728")
        self.ax_filt.axvspan(start_sec, end_sec, alpha=0.15, color="red")
        self.ax_filt.set_ylabel("mV")
        self.ax_filt.set_xlabel("Time (s)")
        flo, fhi = self.freq_lo_var.get(), self.freq_hi_var.get()
        self.ax_filt.set_title(
            f"{fname} — Ch {cid} — Bandpass {flo:.0f}–{fhi:.0f} Hz", fontsize=10
        )

        self.canvas.draw_idle()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _status(self, text: str):
        self.status_bar.config(text=text)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = StepFinderApp(root)
    root.mainloop()
