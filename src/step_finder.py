"""
Step Noise Finder — scans all channels in a TDD baseline file and locates
regions with periodic step noise (4-20 Hz, configurable amplitude).

Standalone GUI application.  Reuses tdd_reader.py from the same src/ folder.
"""
import sys
import os
import glob
import threading

# PyInstaller windowed-app guard
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from scipy.signal import butter, filtfilt
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from tdd_reader import parse_header, load_data, descramble_channels, TDDHeader


# ── Detection core ────────────────────────────────────────────────────────────

def _bandpass(data: np.ndarray, fs: float, lo: float, hi: float, order: int = 2):
    """Zero-phase Butterworth bandpass."""
    nyq = fs / 2.0
    b, a = butter(order, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, data)


def find_step_noise(
    channel_uv: np.ndarray,
    sample_rate: float,
    freq_lo: float = 4.0,
    freq_hi: float = 20.0,
    min_amplitude_mv: float = 100.0,
    min_periodicity: float = 0.4,
    window_sec: float = 10.0,
    overlap: float = 0.5,
):
    """
    Detect time segments with periodic step noise.

    Returns a list of dicts with keys:
        start_sec, end_sec, frequency_hz, amplitude_mv, periodicity
    """
    # Bandpass isolate the step-noise frequency range (with a little margin)
    filt = _bandpass(channel_uv, sample_rate,
                     max(0.5, freq_lo - 1.0), min(sample_rate / 2 - 1, freq_hi + 5.0))

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

        # Autocorrelation to check periodicity
        seg_n = seg - seg.mean()
        ac = np.correlate(seg_n, seg_n, mode="full")
        ac = ac[len(seg_n) - 1 :]          # positive lags only
        ac /= ac[0] + 1e-20                # normalise

        ac_band = ac[min_lag : max_lag + 1]
        if len(ac_band) == 0:
            continue

        peak_ac = float(ac_band.max())
        if peak_ac < min_periodicity:
            continue

        peak_lag = int(np.argmax(ac_band)) + min_lag
        freq = sample_rate / peak_lag

        # Baseline = mean of raw (unfiltered) signal in this window
        raw_seg = channel_uv[start : start + window_n]
        baseline_mv = round(float(raw_seg.mean()) / 1000.0, 1)

        raw_hits.append(
            {
                "start_sec": start / sample_rate,
                "end_sec": (start + window_n) / sample_rate,
                "frequency_hz": round(freq, 1),
                "amplitude_mv": round(pp_mv, 1),
                "periodicity": round(peak_ac, 3),
                "baseline_mv": baseline_mv,
            }
        )

    # Merge overlapping / adjacent windows into contiguous segments
    if not raw_hits:
        return []

    merged = [raw_hits[0].copy()]
    for h in raw_hits[1:]:
        prev = merged[-1]
        if h["start_sec"] <= prev["end_sec"]:
            prev["end_sec"] = max(prev["end_sec"], h["end_sec"])
            prev["amplitude_mv"] = max(prev["amplitude_mv"], h["amplitude_mv"])
            prev["periodicity"] = max(prev["periodicity"], h["periodicity"])
            prev["frequency_hz"] = round(
                (prev["frequency_hz"] + h["frequency_hz"]) / 2, 1
            )
            prev["baseline_mv"] = round(
                (prev["baseline_mv"] + h["baseline_mv"]) / 2, 1
            )
        else:
            merged.append(h.copy())

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
        # Folder-scan: cache of loaded file data for plotting
        self._file_cache: dict = {}      # filepath -> (header, data, chan_map)

        self._build_ui()
        self._status("Ready — open a TDD file to begin.")

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # Menu
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Open TDD…", command=self._open_file,
                              accelerator="Ctrl+O")
        file_menu.add_command(label="Scan Files…", command=self._open_files,
                              accelerator="Ctrl+Shift+O")
        file_menu.add_command(label="Scan Folder…", command=self._open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menu.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menu)
        self.root.bind_all("<Control-o>", lambda e: self._open_file())
        self.root.bind_all("<Control-Shift-O>", lambda e: self._open_files())

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

        self.scan_btn = ttk.Button(top, text="Scan All Channels",
                                   command=self._start_scan, state="disabled")
        self.scan_btn.pack(side="left", padx=4)

        self.folder_btn = ttk.Button(top, text="Scan Baselines…",
                                     command=self._open_folder)
        self.files_btn = ttk.Button(top, text="Pick Files…",
                                    command=self._open_files)
        self.files_btn.pack(side="left", padx=4)
        self.folder_btn.pack(side="left", padx=4)

        # Progress
        prog_frame = ttk.Frame(self.root, padding=(6, 0))
        prog_frame.pack(fill="x")
        self.progress = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress.pack(fill="x", side="left", expand=True)
        self.prog_lbl = ttk.Label(prog_frame, text="", width=30)
        self.prog_lbl.pack(side="left", padx=6)

        # Main area — PanedWindow: results table (left) + plot (right)
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=6)

        # Results table
        tbl_frame = ttk.Frame(pane)
        pane.add(tbl_frame, weight=1)

        cols = ("file", "channel", "start", "end", "freq", "amp", "baseline", "periodicity")
        self.tree = ttk.Treeview(tbl_frame, columns=cols, show="headings",
                                 selectmode="browse")
        self.tree.heading("file", text="File")
        self.tree.heading("channel", text="Channel")
        self.tree.heading("start", text="Start (s)")
        self.tree.heading("end", text="End (s)")
        self.tree.heading("freq", text="Freq (Hz)")
        self.tree.heading("amp", text="Amp (mV)")
        self.tree.heading("baseline", text="Baseline (mV)")
        self.tree.heading("periodicity", text="Periodicity")
        for c in cols:
            self.tree.column(c, width=85, anchor="center")
        self.tree.column("file", width=180, anchor="w")
        self.tree.column("channel", width=70)
        self.tree.column("baseline", width=95)

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

    # ── File handling ─────────────────────────────────────────────────────

    def _load_tdd(self, fp: str):
        """Load a TDD file and cache it. Returns (header, data, chan_map)."""
        if fp in self._file_cache:
            return self._file_cache[fp]
        header = parse_header(fp)
        data, _ = load_data(fp, header)
        chan_map = descramble_channels(header.channel_ids)
        self._file_cache[fp] = (header, data, chan_map)
        return header, data, chan_map

    def _open_file(self):
        fp = filedialog.askopenfilename(
            title="Open TDD baseline file",
            filetypes=[("TDD files", "*.tdd"), ("All files", "*.*")],
        )
        if not fp:
            return
        try:
            self._file_cache.clear()
            header, data, chan_map = self._load_tdd(fp)
            self.header = header
            self.data = data
            self.chan_map = chan_map
            self.filepath = fp
            dur = data.shape[0] / header.sample_rate
            self.file_lbl.config(
                text=f"{os.path.basename(fp)}  ({header.channel_count} ch, "
                     f"{header.sample_rate} Hz, {dur:.0f} s)"
            )
            self.scan_btn.config(state="normal")
            self._status(f"Loaded {os.path.basename(fp)} — {header.channel_count} "
                         f"channels, {dur:.0f}s.  Press Scan.")
            self.tree.delete(*self.tree.get_children())
            self.results.clear()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to open file:\n{exc}")

    def _open_files(self):
        files = filedialog.askopenfilenames(
            title="Select Baseline TDD files to scan",
            filetypes=[("Baseline TDD files", "*Baseline*.tdd"),
                       ("All TDD files", "*.tdd")],
        )
        if not files:
            return
        self._launch_batch_scan(sorted(files))

    def _open_folder(self):
        folder = filedialog.askdirectory(title="Select folder with Baseline TDD files")
        if not folder:
            return
        tdd_files = sorted(glob.glob(os.path.join(folder, "*Baseline*.tdd")))
        if not tdd_files:
            messagebox.showinfo("No files",
                                "No *Baseline*.tdd files found in the selected folder.")
            return
        self._launch_batch_scan(tdd_files)

    def _launch_batch_scan(self, tdd_files: list):
        self._file_cache.clear()
        self.filepath = None
        self.header = None
        self.data = None
        self.chan_map = []
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.scan_btn.config(state="disabled")
        folder = os.path.dirname(tdd_files[0])
        self.file_lbl.config(
            text=f"{os.path.basename(folder)}/  ({len(tdd_files)} Baseline files)")
        self._status(f"Scanning {len(tdd_files)} file(s) in {os.path.basename(folder)}…")
        self._start_folder_scan(tdd_files)

    # ── Scanning ──────────────────────────────────────────────────────────

    def _start_scan(self):
        if self._scanning or self.data is None:
            return
        self._scanning = True
        self.scan_btn.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.progress["value"] = 0
        self.progress["maximum"] = len(self.chan_map)
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
        threading.Thread(target=self._folder_scan_worker, args=(tdd_files,),
                         daemon=True).start()

    def _scan_worker(self, filepath, header, data, chan_map):
        amp = self.amp_var.get()
        flo = self.freq_lo_var.get()
        fhi = self.freq_hi_var.get()
        per = self.per_var.get()
        total_ch = len(chan_map)
        found = []

        for i, cm in enumerate(chan_map):
            cid, col = cm["cid"], cm["col"]
            ch_data = data[:, col].astype(np.float64)

            segments = find_step_noise(
                ch_data, header.sample_rate,
                freq_lo=flo, freq_hi=fhi,
                min_amplitude_mv=amp, min_periodicity=per,
            )
            if segments:
                found.append((filepath, cid, col, segments))

            self.root.after(0, self._update_progress, i + 1, total_ch,
                            f"Ch {cid}")

        self.root.after(0, self._scan_done, found)

    def _folder_scan_worker(self, tdd_files: list):
        amp = self.amp_var.get()
        flo = self.freq_lo_var.get()
        fhi = self.freq_hi_var.get()
        per = self.per_var.get()
        n_files = len(tdd_files)
        all_found = []

        for fi, fp in enumerate(tdd_files):
            fname = os.path.basename(fp)
            try:
                header, data, chan_map = self._load_tdd(fp)
            except Exception:
                self.root.after(0, self._update_progress, fi + 1, n_files,
                                f"SKIP {fname}")
                continue

            total_ch = len(chan_map)
            for ci, cm in enumerate(chan_map):
                cid, col = cm["cid"], cm["col"]
                ch_data = data[:, col].astype(np.float64)

                segments = find_step_noise(
                    ch_data, header.sample_rate,
                    freq_lo=flo, freq_hi=fhi,
                    min_amplitude_mv=amp, min_periodicity=per,
                )
                if segments:
                    all_found.append((fp, cid, col, segments))

                self.root.after(
                    0, self._update_progress,
                    fi * total_ch + ci + 1,
                    n_files * total_ch,
                    f"{fname}  Ch {cid}",
                )

            # Free memory for files we're done scanning (keep only those with hits)
            hit_fps = {f[0] for f in all_found}
            for cached_fp in list(self._file_cache.keys()):
                if cached_fp != fp and cached_fp not in hit_fps:
                    del self._file_cache[cached_fp]

        self.root.after(0, self._scan_done, all_found)

    def _update_progress(self, done: int, total: int, label: str):
        self.progress["maximum"] = total
        self.progress["value"] = done
        self.prog_lbl.config(text=f"{label}  ({done}/{total})")

    def _scan_done(self, found: list):
        self._scanning = False
        self.scan_btn.config(state="normal" if self.data is not None else "disabled")
        self.folder_btn.config(state="normal")
        self.results = found
        self.prog_lbl.config(text="")

        # Populate table
        for filepath, cid, col, segments in found:
            fname = os.path.basename(filepath)
            for seg in segments:
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
                        seg["periodicity"],
                    ),
                    tags=(filepath, str(col)),
                )

        n_files = len({f[0] for f in found})
        n_ch = len({(f[0], f[1]) for f in found})
        n_seg = sum(len(s) for _, _, _, s in found)
        self._status(f"Scan complete — {n_seg} segment(s) across "
                     f"{n_ch} channel(s) in {n_files} file(s).")

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
        baseline_mv = float(vals[6])
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
        s0 = max(0, int(start_sec * fs) - int(5 * fs))   # 5 s context
        s1 = min(data.shape[0], int(end_sec * fs) + int(5 * fs))

        raw_uv = data[s0:s1, col].astype(np.float64)
        t = np.arange(s0, s1) / fs

        filt_uv = _bandpass(
            raw_uv, fs,
            max(0.5, self.freq_lo_var.get() - 1),
            min(fs / 2 - 1, self.freq_hi_var.get() + 5),
        )

        raw_mv = raw_uv / 1000
        filt_mv = filt_uv / 1000

        # Raw plot
        self.ax_raw.clear()
        self.ax_raw.plot(t, raw_mv, linewidth=0.5, color="#1f77b4")
        self.ax_raw.axvspan(start_sec, end_sec, alpha=0.15, color="red",
                            label="Step noise")
        self.ax_raw.axhline(baseline_mv, color="#2ca02c", linewidth=1,
                            linestyle="--", label=f"Baseline {baseline_mv:.1f} mV")
        self.ax_raw.set_ylabel("mV")
        self.ax_raw.set_title(f"{fname} — Ch {cid} — Raw signal", fontsize=10)
        self.ax_raw.legend(loc="upper right", fontsize=8)

        # Filtered plot
        self.ax_filt.clear()
        self.ax_filt.plot(t, filt_mv, linewidth=0.5, color="#d62728")
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
