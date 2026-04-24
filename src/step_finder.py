"""
Step Noise Finder — scans all channels in a TDD baseline file and locates
regions with periodic step noise (4-20 Hz, configurable amplitude).

Standalone GUI application.  Reuses tdd_reader.py from the same src/ folder.
"""
import sys
import os
import glob
import fnmatch
import threading
from datetime import datetime

# PyInstaller windowed-app guard
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks
from scipy.fft import rfft, irfft, next_fast_len
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from tdd_reader import parse_header, load_data, descramble_channels, TDDHeader


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
    min_periodicity: float = 0.4,
    window_sec: float = 10.0,
    overlap: float = 0.5,
    max_gap_sec: float = 30.0,
    filter_coefs: tuple | None = None,
):
    """
    Detect time segments with periodic step noise.

    Returns a list of dicts with keys:
        start_sec, end_sec, frequency_hz, amplitude_mv, periodicity

    filter_coefs: optional pre-computed (b, a) from compute_bandpass_coefs().
        When scanning many channels at the same rate, pass this in to skip
        re-designing the Butterworth filter per channel.
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

        # Reject single level shifts: true periodic signals have autocorrelation
        # peaks at multiples of the fundamental lag (2x, 3x).  A one-off step
        # only rings once in the bandpass filter and won't show a 2nd-harmonic peak.
        lag_2x = 2 * peak_lag
        if lag_2x < len(ac):
            ac_2nd = float(ac[lag_2x])
            if ac_2nd < 0.15:
                continue
        else:
            continue

        freq = sample_rate / peak_lag

        # Reject single impulses / transients by counting actual oscillations.
        # A real periodic signal at `freq` Hz will have ~freq * window_sec
        # positive peaks above a reasonable height. A single spike + ringdown
        # has only 1-2 peaks even if autocorrelation gets fooled by filter
        # ringing. Require at least 30% of expected peak count.
        peak_height = 0.2 * (pp_mv * 1000.0)   # 20% of pp, in µV
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
            seg["baseline_mv"] = 0.0

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
        root.title("Step Noise Finder")
        root.geometry("1280x820")
        root.minsize(960, 600)

        # State
        self.filepath: str | None = None
        self.header: TDDHeader | None = None
        self.data: np.ndarray | None = None
        self.chan_map: list = []
        self.results: list = []          # list of (cid, col, segments) per file
        self._scanning = False
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
        self._status("Ready — open a TDD file to begin.")

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # Menu
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Open TDD…", command=self._open_file,
                              accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Scan Baselines…", command=self._scan_folder,
                              accelerator="Ctrl+Shift+O")
        file_menu.add_command(label="Scan Files…", command=self._scan_files)
        file_menu.add_command(label="Scan Folders…", command=self._scan_multi_folders)
        file_menu.add_command(label="Scan All Runs…", command=self._scan_data_root)
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
        self.per_var = tk.DoubleVar(value=0.4)
        ttk.Spinbox(top, textvariable=self.per_var, from_=0.1, to=0.95,
                     increment=0.05, width=5, format="%.2f").pack(side="left", padx=(2, 12))

        ttk.Label(top, text="Gap (s):").pack(side="left")
        self.gap_var = tk.DoubleVar(value=30.0)
        ttk.Spinbox(top, textvariable=self.gap_var, from_=0, to=300,
                     increment=5, width=5).pack(side="left", padx=(2, 12))

        self.scan_btn = ttk.Button(top, text="Scan All Channels",
                                   command=self._start_scan, state="disabled")
        self.scan_btn.pack(side="left", padx=4)

        self.folder_btn = ttk.Button(top, text="Scan Baselines…",
                                     command=self._scan_folder)
        self.folder_btn.pack(side="left", padx=4)

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
                "pre_baseline", "post_baseline", "periodicity")
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

        vsb = ttk.Scrollbar(tbl_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

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
        if self._scanning or self.data is None:
            return
        self._scanning = True
        self._batch_folder = os.path.dirname(self.filepath)
        self._batch_files = [self.filepath]
        self.scan_btn.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.chan_map)
        self._start_busy_indicator()
        threading.Thread(target=self._scan_worker,
                         args=(self.filepath, self.header, self.data, self.chan_map),
                         daemon=True).start()

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
        self._tick_busy_indicator()

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
            )
            return cid_, col_, segs

        max_workers = min(os.cpu_count() or 1, 8)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_scan_one, cm) for cm in chan_map]
            done = 0
            for fut in as_completed(futures):
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
        n_files = len(tdd_files)
        all_found = []
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
            fname = self._short_name(fp)
            try:
                header, data, chan_map = self._load_tdd(fp)
            except Exception:
                self.root.after(0, self._update_progress, fi + 1, n_files,
                                f"SKIP {fname}")
                chan_map = None

            if chan_map is not None:
                total_ch = len(chan_map)
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
                    )
                    return _fp, cid_, col_, segs

                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futures = [ex.submit(_scan_one, cm) for cm in chan_map]
                    ci_done = 0
                    for fut in as_completed(futures):
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
                if folder_found:
                    rp = self._write_report(folder_found, cur_folder, folder_files)
                    if rp:
                        report_paths.append(rp)
                        self.root.after(0, self._status,
                                        f"Report written: {os.path.basename(rp)}")
                folder_idx += 1
                files_done_in_folder = 0

        self.root.after(0, self._scan_done, all_found, report_paths)

    def _update_progress(self, done: int, total: int, label: str):
        self.progress["maximum"] = total
        self.progress["value"] = done
        self.prog_lbl.config(text=f"{label}  ({done}/{total})")

    def _scan_done(self, found: list, report_paths: list | None = None):
        self._stop_busy_indicator()
        self.scan_btn.config(state="normal" if self.data is not None else "disabled")
        self.folder_btn.config(state="normal")
        self.results = found
        self.prog_lbl.config(text="")

        # Populate table
        for filepath, cid, col, segments in found:
            fname = self._short_name(filepath)
            for seg in segments:
                post_bl = (f"{seg['post_baseline_mv']}"
                           if seg["post_baseline_mv"] is not None else "—")
                self.tree.insert(
                    "", "end",
                    values=(
                        fname,
                        cid,
                        f"{seg['start_sec']:.1f}",
                        f"{seg['end_sec']:.1f}",
                        seg["frequency_hz"],
                        seg["amplitude_mv"],
                        seg["baseline_mv"],
                        post_bl,
                        seg["periodicity"],
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
                        f.write(
                            f"{fname:<16} {cid:>4} {seg['start_sec']:>8.1f} "
                            f"{seg['end_sec']:>8.1f} {seg['frequency_hz']:>6.1f} "
                            f"{seg['amplitude_mv']:>8.1f} {seg['baseline_mv']:>8.1f} "
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
        pre_bl_mv = float(vals[6])
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
        self.ax_raw.axhline(pre_bl_mv, color="#2ca02c", linewidth=1,
                            linestyle="--", label=f"Pre BL {pre_bl_mv:.1f} mV")
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
