"""
Step Noise Detection — GUI Application
Tkinter + Matplotlib desktop app for analysing TDD files.
"""
import os
import time
import threading
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.cm as cm
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.patches import Patch

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from tdd_reader import (
    parse_header, load_data, get_total_samples,
    descramble_channels, TDDHeader,
)
from noise_detector import (
    detect_rms_noise, detect_edges, compute_fft_chunks,
    EdgeEvent, RMSResult,
)


# ── Utility ──────────────────────────────────────────────────────────────────

def _make_plot_area(parent, nrows=1, figsize=(10, 5)):
    """Create a matplotlib figure embedded in *parent*. Returns (fig, canvas, axes_list)."""
    frame = ttk.Frame(parent)
    frame.pack(fill='both', expand=True)
    fig = Figure(figsize=figsize, dpi=96,
                 tight_layout={'pad': 1.5, 'w_pad': 1.0, 'h_pad': 1.0})
    canvas = FigureCanvasTkAgg(fig, master=frame)
    canvas.get_tk_widget().pack(fill='both', expand=True)
    tb = NavigationToolbar2Tk(canvas, frame)
    tb.update()
    axes = [fig.add_subplot(nrows, 1, i + 1) for i in range(nrows)]
    return fig, canvas, axes


# ── Main application ─────────────────────────────────────────────────────────

class StepNoiseApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Step Noise Detection")
        self.root.geometry("1440x900")
        self.root.minsize(1100, 700)

        # Data state
        self.filepath:       str | None      = None
        self.header:         TDDHeader | None = None
        self.data:           np.ndarray | None = None  # (samples, channels) float32 μV
        self.total_samples:  int = 0
        self.time_axis:      np.ndarray | None = None
        # Descrambled channel map: list of {'cid': int, 'col': int} sorted by cid
        self.chan_map:        list = []

        # Analysis results  {channel_id -> result}
        self.rms_results:   dict = {}
        self.edge_results:  dict = {}
        self.fft_results:   dict = {}
        self.fft_chunk_idx: int  = 0

        self._build_ui()
        self._set_status("Ready — open a TDD file to begin.")

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()
        pane = ttk.PanedWindow(self.root, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        left = ttk.Frame(pane, width=290)
        left.pack_propagate(False)
        pane.add(left, weight=0)
        right = ttk.Frame(pane)
        pane.add(right, weight=1)
        self._build_settings_panel(left)
        self._build_notebook(right)

    # ── Menu ─────────────────────────────────────────────────────────────────

    def _build_menu(self):
        mb = tk.Menu(self.root)

        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="Open TDD File…", command=self.open_file,
                       accelerator="Ctrl+O")
        fm.add_separator()
        fm.add_command(label="Exit", command=self.root.quit)
        mb.add_cascade(label="File", menu=fm)

        am = tk.Menu(mb, tearoff=0)
        am.add_command(label="Run All Detection",       command=self.run_all)
        am.add_separator()
        am.add_command(label="FFT Analysis", command=self.run_fft_only)
        am.add_command(label="RMS Detection",           command=self.run_rms_only)
        am.add_command(label="Edge Detection",          command=self.run_edge_only)
        mb.add_cascade(label="Analysis", menu=am)

        rm = tk.Menu(mb, tearoff=0)
        rm.add_command(label="Generate Report…", command=self.generate_report)
        mb.add_cascade(label="Report", menu=rm)

        self.root.config(menu=mb)
        self.root.bind('<Control-o>', lambda _: self.open_file())

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = ttk.Frame(self.root, relief='raised', padding=4)
        tb.pack(side='top', fill='x')

        ttk.Button(tb, text="Open TDD",       command=self.open_file).pack(side='left', padx=2)
        ttk.Separator(tb, orient='vertical').pack(side='left', fill='y', padx=6)
        ttk.Button(tb, text="Run Analysis",   command=self.run_all).pack(side='left', padx=2)
        ttk.Button(tb, text="Generate Report",command=self.generate_report).pack(side='left', padx=2)
        ttk.Separator(tb, orient='vertical').pack(side='left', fill='y', padx=6)

        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(tb, variable=self.progress_var,
                         maximum=100, length=180).pack(side='left', padx=4)

        self.status_var = tk.StringVar(value="")
        ttk.Label(tb, textvariable=self.status_var).pack(side='left', padx=6)

    # ── Settings panel (left) ────────────────────────────────────────────────

    def _build_settings_panel(self, parent):
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>",
                lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=sf, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        def section(text):
            f = ttk.LabelFrame(sf, text=text, padding=6)
            f.pack(fill='x', padx=4, pady=3)
            return f

        def row(parent, label, var, widget='entry', **kw):
            ttk.Label(parent, text=label).pack(anchor='w')
            if widget == 'entry':
                ttk.Entry(parent, textvariable=var, width=14).pack(anchor='w', pady=1)
            elif widget == 'spin':
                ttk.Spinbox(parent, textvariable=var, width=10, **kw).pack(anchor='w', pady=1)
            elif widget == 'combo':
                ttk.Combobox(parent, textvariable=var, state='readonly',
                             width=16, **kw).pack(anchor='w', pady=1)

        # ── File info ────────────────────────────────────────────────────────
        info = section("File Info")
        self.info_vars = {}
        for key in ('File', 'Channels', 'Sample Rate', 'Duration', 'Sample ID'):
            r = ttk.Frame(info)
            r.pack(fill='x')
            ttk.Label(r, text=f"{key}:", width=12, anchor='w').pack(side='left')
            lbl = ttk.Label(r, text="—", anchor='w', wraplength=155)
            lbl.pack(side='left', fill='x', expand=True)
            self.info_vars[key] = lbl

        # ── Channel selection ────────────────────────────────────────────────
        ch = section("Channel Selection")
        ttk.Label(ch, text="Active channel:").pack(anchor='w')
        self.channel_var   = tk.StringVar()
        self.channel_combo = ttk.Combobox(ch, textvariable=self.channel_var,
                                          state='readonly', width=22)
        self.channel_combo.pack(fill='x', pady=2)
        self.channel_combo.bind('<<ComboboxSelected>>', self._on_channel_changed)

        nav = ttk.Frame(ch)
        nav.pack(fill='x', pady=2)
        ttk.Button(nav, text="◀ Prev",
                   command=self._single_ch_prev).pack(side='left', padx=2)
        ttk.Button(nav, text="Next ▶",
                   command=self._single_ch_next).pack(side='left', padx=2)

        # ── Data load range ──────────────────────────────────────────────────
        lr = section("Data Load Range")
        self.load_minutes_var = tk.DoubleVar(value=0.0)
        row(lr, "Max duration (min, 0=all):", self.load_minutes_var)
        ttk.Button(lr, text="Reload Data", command=self._reload_data).pack(anchor='w', pady=2)

        # ── Edge detection ───────────────────────────────────────────────────
        ed = section("Edge Detection")
        self.edge_amp_var       = tk.DoubleVar(value=50.0)
        self.edge_min_width_var = tk.DoubleVar(value=0.05)
        self.edge_max_width_var = tk.DoubleVar(value=60.0)
        row(ed, "Amplitude threshold (μV):", self.edge_amp_var)
        row(ed, "Min plateau width (s):",    self.edge_min_width_var)
        row(ed, "Max plateau width (s):",    self.edge_max_width_var)
        ttk.Button(ed, text="Run Edge Detection",
                   command=self.run_edge_only).pack(anchor='w', pady=2)

        # ── RMS ──────────────────────────────────────────────────────────────
        rm = section("RMS Detection (auto)")
        self.rms_window_var = tk.DoubleVar(value=1.0)
        self.rms_thresh_var = tk.DoubleVar(value=3.0)
        row(rm, "Window size (s):",         self.rms_window_var)
        row(rm, "Threshold multiplier:",    self.rms_thresh_var)
        ttk.Button(rm, text="Run RMS Detection",
                   command=self.run_rms_only).pack(anchor='w', pady=2)

        # ── FFT ──────────────────────────────────────────────────────────────
        ff = section("FFT")
        self.fft_chunk_var  = tk.DoubleVar(value=5.0)
        self.fft_window_var = tk.StringVar(value='hann')
        self.fft_maxf_var   = tk.DoubleVar(value=0.0)
        row(ff, "Chunk duration (min):",    self.fft_chunk_var)
        row(ff, "Window function:",         self.fft_window_var, widget='combo',
            values=['hann', 'hamming', 'blackman', 'rectangular', 'flattop'])
        row(ff, "Max freq (Hz, 0=auto):",   self.fft_maxf_var)
        ttk.Button(ff, text="Compute FFT",
                   command=self.run_fft_only).pack(anchor='w', pady=2)

        # ── Overview plot controls ────────────────────────────────────────────
        ov = section("Overview Plot")
        self.ov_n_chan_var  = tk.IntVar(value=256)
        self.ov_offset_var  = tk.DoubleVar(value=500.0)
        self.ov_rms_var     = tk.BooleanVar(value=True)
        self.ov_edge_var    = tk.BooleanVar(value=True)
        self.ov_spin = ttk.Spinbox(ov, textvariable=self.ov_n_chan_var,
                                    from_=1, to=256, width=10)
        ttk.Label(ov, text="Channels to show:").pack(anchor='w')
        self.ov_spin.pack(anchor='w', pady=1)
        row(ov, "Stack offset (μV):", self.ov_offset_var)
        ttk.Checkbutton(ov, text="Highlight RMS regions",
                        variable=self.ov_rms_var).pack(anchor='w')
        ttk.Checkbutton(ov, text="Mark edge events",
                        variable=self.ov_edge_var).pack(anchor='w')
        ttk.Button(ov, text="Refresh Overview",
                   command=self.plot_overview).pack(anchor='w', pady=2)

    # ── Notebook (right) ─────────────────────────────────────────────────────

    def _build_notebook(self, parent):
        self.nb = ttk.Notebook(parent)
        self.nb.pack(fill='both', expand=True)

        tabs = [
            ("Metadata",           self._build_metadata_tab),
            ("Overview",           self._build_overview_tab),
            ("Single Channel",     self._build_single_ch_tab),
            ("Edge Detection",     self._build_edge_tab),
            ("RMS Detection",      self._build_rms_tab),
            ("FFT",                self._build_fft_tab),
        ]
        for label, builder in tabs:
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=label)
            builder(frame)

    # ── Metadata tab ─────────────────────────────────────────────────────────

    def _build_metadata_tab(self, parent):
        # Scrollable frame
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        sf = ttk.Frame(canvas)
        sf.bind("<Configure>",
                lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=sf, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        # Title
        ttk.Label(sf, text="TDD File Metadata", font=('', 14, 'bold')
                  ).pack(anchor='w', padx=12, pady=(12, 6))

        # The table will be built here by _populate_metadata_tab
        self._meta_frame = ttk.Frame(sf)
        self._meta_frame.pack(fill='both', expand=True, padx=12, pady=6)

        self._meta_placeholder = ttk.Label(
            self._meta_frame, text="No file loaded.",
            font=('', 10), foreground='gray')
        self._meta_placeholder.pack(anchor='w', pady=20)

    def _populate_metadata_tab(self):
        """Fill the metadata tab with header fields from the loaded TDD file."""
        # Clear old content
        for w in self._meta_frame.winfo_children():
            w.destroy()

        if self.header is None:
            ttk.Label(self._meta_frame, text="No file loaded.",
                      font=('', 10), foreground='gray').pack(anchor='w', pady=20)
            return

        h  = self.header
        sr = h.sample_rate
        n  = self.data.shape[0] if self.data is not None else 0
        dur = n / sr if sr > 0 else 0

        # Organize fields into sections
        sections = [
            ("File", [
                ("File",             os.path.basename(self.filepath)),
                ("File Version",     str(h.file_version)),
                ("File Type",        f"{h.file_type:#010x}"),
                ("File Number",      str(h.file_number)),
            ]),
            ("System", [
                ("System ID",        h.system_id),
                ("Software",         h.software),
                ("Software Version", h.software_version),
                ("Controller SW",    h.system_controller_sw_version),
                ("Module Number",    str(h.module_number)),
                ("Module Model",     h.module_model_number),
                ("Module Serial",    h.module_serial_number),
            ]),
            ("Detector", [
                ("Detector Type",    h.detector_type),
                ("Lot Number",       h.detector_lot_number),
                ("Wafer ID",         h.detector_wafer_id),
                ("Die Number",       h.detector_die_number),
            ]),
            ("Sample", [
                ("Sample ID",        h.sample_id),
                ("Scientist",        h.scientist_name),
                ("Sample Type",      h.sample_type),
                ("Description",      h.sample_description),
                ("Method",           h.sample_method),
                ("Tag Size",         h.tag_size),
                ("Reagent Lot",      h.reagent_lot),
            ]),
            ("Run", [
                ("Operator",         h.operator),
                ("Run ID",           h.run_id),
                ("Protocol",         h.protocol_name),
                ("Protocol Version", h.protocol_version),
                ("Settings Group",   h.settings_group),
                ("Settings Version", h.settings_version),
            ]),
            ("Data", [
                ("Channels",         str(h.channel_count)),
                ("Sample Rate",      f"{sr} Hz"),
                ("Duration",         f"{dur:.1f} s  ({dur / 60:.2f} min)"),
                ("Samples Loaded",   f"{n:,}"),
                ("Segment Length",   str(h.segment_length)),
                ("Factor Range",     f"{min(h.factors):.4f} – {max(h.factors):.4f} μV/count"),
                ("LP Filter Range",  f"{min(h.low_pass_filter):.1f} – {max(h.low_pass_filter):.1f} Hz"),
                ("HP Filter Range",  f"{min(h.high_pass_filter):.4f} – {max(h.high_pass_filter):.4f} Hz"),
            ]),
        ]

        for sec_name, fields in sections:
            lf = ttk.LabelFrame(self._meta_frame, text=sec_name, padding=8)
            lf.pack(fill='x', pady=(0, 8))

            for i, (label, value) in enumerate(fields):
                row = ttk.Frame(lf)
                row.pack(fill='x', pady=1)
                bg = '#f0f4fa' if i % 2 == 0 else None

                lbl = ttk.Label(row, text=f"{label}:", width=20, anchor='w',
                                font=('', 9, 'bold'))
                lbl.pack(side='left')

                val = ttk.Label(row, text=value or "—", anchor='w',
                                font=('', 9), wraplength=600)
                val.pack(side='left', fill='x', expand=True)

    # ── Overview tab ─────────────────────────────────────────────────────────

    def _build_overview_tab(self, parent):
        self.fig_ov, self.canvas_ov, axes = _make_plot_area(parent, figsize=(11, 6))
        self.ax_ov = axes[0]
        # State for interactive click-on-trace
        self._ov_indices = []       # channel-array indices currently plotted
        self._ov_lines   = []       # matplotlib Line2D objects, one per trace
        self._ov_offset_used = 0.0  # y offset that was used for the current plot
        self._ov_last_click_time = 0.0
        self._ov_dblclick_ms = 400   # max ms between clicks to count as double
        self.fig_ov.canvas.mpl_connect(
            'button_press_event', self._on_overview_click)

    # ── Single Channel tab ──────────────────────────────────────────────────

    def _build_single_ch_tab(self, parent):
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill='x', padx=4, pady=2)

        self.single_ch_var = tk.StringVar(value="—")
        ttk.Label(ctrl, textvariable=self.single_ch_var,
                  font=('', 10, 'bold'), width=20).pack(side='left', padx=4)

        ttk.Separator(ctrl, orient='vertical').pack(side='left', fill='y', padx=6)
        ttk.Label(ctrl, text="Time start (s):").pack(side='left')
        self.sc_tstart_var = tk.DoubleVar(value=0.0)
        ttk.Entry(ctrl, textvariable=self.sc_tstart_var, width=8).pack(side='left', padx=2)
        ttk.Label(ctrl, text="Duration (s, 0=all):").pack(side='left')
        self.sc_tdur_var = tk.DoubleVar(value=0.0)
        ttk.Entry(ctrl, textvariable=self.sc_tdur_var, width=8).pack(side='left', padx=2)
        ttk.Button(ctrl, text="Refresh", command=self.plot_single_ch).pack(side='left', padx=6)
        ttk.Separator(ctrl, orient='vertical').pack(side='left', fill='y', padx=6)
        ttk.Button(ctrl, text="Run RMS",
                   command=self._run_rms_and_replot_sc).pack(side='left', padx=2)
        ttk.Button(ctrl, text="Run Edge Detection",
                   command=self._run_edge_and_replot_sc).pack(side='left', padx=2)

        self.fig_sc, self.canvas_sc, axes = _make_plot_area(parent, figsize=(11, 5))
        self.ax_sc = axes[0]

    def _single_ch_prev(self):
        if self.data is None:
            return
        pos = max(0, self._combo_pos() - 1)
        self.channel_combo.current(pos)
        self._on_channel_changed()

    def _single_ch_next(self):
        if self.data is None:
            return
        pos = min(len(self.chan_map) - 1, self._combo_pos() + 1)
        self.channel_combo.current(pos)
        self._on_channel_changed()

    def _run_rms_and_replot_sc(self):
        if not self._require_data():
            return
        def task():
            self._set_status("Running RMS…")
            self._do_rms()
            self.root.after(0, self.plot_single_ch)
            self.root.after(0, lambda: self._set_status("RMS detection done."))
        threading.Thread(target=task, daemon=True).start()

    def _run_edge_and_replot_sc(self):
        if not self._require_data():
            return
        def task():
            self._set_status("Running edge detection…")
            self._do_edges()
            self.root.after(0, self.plot_single_ch)
            self.root.after(0, lambda: self._set_status("Edge detection done."))
        threading.Thread(target=task, daemon=True).start()

    def plot_single_ch(self):
        if self.data is None:
            return
        ci  = self._ch_col()
        cid = self._ch_id()
        sr  = self.header.sample_rate
        ax  = self.ax_sc
        ax.clear()

        t  = self.time_axis
        ch = self.data[:, ci]

        # Optional time window
        t_start_s = self.sc_tstart_var.get()
        t_dur_s   = self.sc_tdur_var.get()
        s = max(0, int(t_start_s * sr))
        if t_dur_s > 0:
            e = min(len(ch), s + int(t_dur_s * sr))
        else:
            e = len(ch)
        t_slice  = t[s:e]
        ch_slice = ch[s:e]

        # Convert μV → mV for display
        ch_mv = ch_slice / 1000.0

        ax.plot(t_slice, ch_mv, color='steelblue', linewidth=0.7)

        # Mark RMS noise regions if available
        if cid in self.rms_results:
            for seg_s, seg_e, _ in self.rms_results[cid].noise_segments:
                seg_s_c = max(seg_s, s)
                seg_e_c = min(seg_e, e)
                if seg_s_c < seg_e_c:
                    ax.axvspan(t[seg_s_c], t[min(seg_e_c, len(t) - 1)],
                               alpha=0.18, color='red')

        # Mark edge events if available
        if cid in self.edge_results:
            for ev in self.edge_results[cid]:
                if s <= ev.start_sample < e:
                    ax.axvline(t[ev.start_sample], color='red',
                               linewidth=0.8, alpha=0.6)

        self.single_ch_var.set(
            f"Channel {cid}  ({ci + 1} / {self.header.channel_count})")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Amplitude (mV)")
        ax.set_title(f"Channel {cid}  —  index {ci + 1}/{self.header.channel_count}")
        ax.tick_params(axis='y', labelsize=8, labelleft=True)
        ax.yaxis.set_label_position('left')
        ax.yaxis.tick_left()
        ax.grid(True, alpha=0.25)
        self.fig_sc.tight_layout(pad=1.5)
        self.canvas_sc.draw_idle()

    # ── RMS Detection tab ────────────────────────────────────────────────────

    def _build_rms_tab(self, parent):
        vpane = ttk.PanedWindow(parent, orient='vertical')
        vpane.pack(fill='both', expand=True)

        plot_frame = ttk.Frame(vpane)
        vpane.add(plot_frame, weight=3)

        ctrl = ttk.Frame(plot_frame)
        ctrl.pack(fill='x', padx=4, pady=2)
        ttk.Button(ctrl, text="Run RMS Detection",
                   command=self.run_rms_only).pack(side='left', padx=4)
        ttk.Separator(ctrl, orient='vertical').pack(side='left', fill='y', padx=6)
        self.rms_info_var = tk.StringVar(value="No results yet.")
        ttk.Label(ctrl, textvariable=self.rms_info_var,
                  font=('', 9)).pack(side='left', padx=4)

        self.fig_rms, self.canvas_rms, axes = _make_plot_area(plot_frame, figsize=(11, 4))
        self.ax_rms = axes[0]

        # Segment table
        tbl_frame = ttk.LabelFrame(vpane, text="Noise Segments")
        vpane.add(tbl_frame, weight=1)
        cols = ('Segment', 'Start (s)', 'End (s)', 'Duration (s)', 'Mean RMS (mV)')
        self.rms_tbl = ttk.Treeview(tbl_frame, columns=cols,
                                     show='headings', height=6)
        for col in cols:
            self.rms_tbl.heading(col, text=col)
            self.rms_tbl.column(col, width=130, anchor='center')
        ys = ttk.Scrollbar(tbl_frame, orient='vertical',
                            command=self.rms_tbl.yview)
        self.rms_tbl.configure(yscrollcommand=ys.set)
        self.rms_tbl.pack(side='left', fill='both', expand=True)
        ys.pack(side='right', fill='y')
        self.rms_tbl.bind('<<TreeviewSelect>>', self._on_rms_segment_select)

    def _refresh_rms_tab(self):
        cid = self._ch_id()
        res = self.rms_results.get(cid)
        sr  = self.header.sample_rate

        # Update info label
        if res:
            self.rms_info_var.set(
                f"Ch {cid}  |  Baseline: {res.baseline_uv / 1000:.4f} mV  |  "
                f"Threshold: {res.threshold_uv / 1000:.4f} mV  |  "
                f"Segments: {len(res.noise_segments)}  |  "
                f"Noise: {res.noise_fraction * 100:.1f}%"
            )
        else:
            self.rms_info_var.set(f"Ch {cid}  |  No results yet. Click 'Run RMS Detection'.")

        # Rebuild table
        self.rms_tbl.delete(*self.rms_tbl.get_children())
        if res:
            for i, (s, e, rms_uv) in enumerate(res.noise_segments):
                dur = (e - s) / sr
                self.rms_tbl.insert('', 'end', values=(
                    i + 1,
                    f"{s / sr:.2f}",
                    f"{e / sr:.2f}",
                    f"{dur:.2f}",
                    f"{rms_uv / 1000:.4f}",
                ))

        # Plot
        self._plot_rms()

    def _plot_rms(self):
        if self.data is None:
            return
        cid = self._ch_id()
        col = self._ch_col()
        res = self.rms_results.get(cid)
        ax  = self.ax_rms
        ax.clear()

        t  = self.time_axis
        ch = self.data[:, col] / 1000.0  # μV → mV

        ax.plot(t, ch, color='steelblue', linewidth=0.5, alpha=0.75, label='Signal')

        if res:
            sr = self.header.sample_rate
            # Draw threshold lines
            ax.axhline(res.baseline_uv / 1000, color='green', linewidth=1,
                       linestyle='--', alpha=0.7, label=f'Baseline {res.baseline_uv / 1000:.4f} mV')
            ax.axhline(res.threshold_uv / 1000, color='orange', linewidth=1,
                       linestyle='--', alpha=0.7, label=f'Threshold {res.threshold_uv / 1000:.4f} mV')
            ax.axhline(-res.threshold_uv / 1000, color='orange', linewidth=1,
                       linestyle='--', alpha=0.7)

            # Shade noise regions
            for s, e, _ in res.noise_segments:
                e_c = min(e, len(t) - 1)
                ax.axvspan(t[s], t[e_c], alpha=0.25, color='red')

            ax.legend(loc='upper right', fontsize=7)
            ax.set_title(
                f"RMS Detection  —  Ch {cid}  |  "
                f"{len(res.noise_segments)} noise segments  |  "
                f"{res.noise_fraction * 100:.1f}% noisy"
            )
        else:
            ax.set_title(f"RMS Detection  —  Ch {cid}  |  No results")

        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Amplitude (mV)")
        ax.grid(True, alpha=0.25)
        self.canvas_rms.draw_idle()

    def _on_rms_segment_select(self, _=None):
        """Zoom the RMS plot to the selected noise segment."""
        sel = self.rms_tbl.selection()
        if not sel or self.data is None:
            return
        cid = self._ch_id()
        res = self.rms_results.get(cid)
        if not res:
            return
        vals = self.rms_tbl.item(sel[0])['values']
        seg_idx = int(vals[0]) - 1
        if seg_idx < 0 or seg_idx >= len(res.noise_segments):
            return
        s, e, _ = res.noise_segments[seg_idx]
        sr = self.header.sample_rate
        # Add 5 seconds context
        ctx = int(5.0 * sr)
        t_s = max(0, s - ctx) / sr
        t_e = min(len(self.data), e + ctx) / sr
        self.ax_rms.set_xlim(t_s, t_e)
        self.canvas_rms.draw_idle()

    # ── FFT tab ──────────────────────────────────────────────────────────────

    def _build_fft_tab(self, parent):
        ctrl = ttk.Frame(parent)
        ctrl.pack(fill='x', padx=4, pady=2)
        ttk.Label(ctrl, text="Chunk:").pack(side='left')
        self.fft_nav_var = tk.StringVar(value="— / —")
        ttk.Label(ctrl, textvariable=self.fft_nav_var, width=8).pack(side='left')
        ttk.Button(ctrl, text="◀ Prev", command=self._fft_prev).pack(side='left', padx=2)
        ttk.Button(ctrl, text="Next ▶", command=self._fft_next).pack(side='left', padx=2)
        ttk.Separator(ctrl, orient='vertical').pack(side='left', fill='y', padx=4)
        self.fft_log_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctrl, text="Log Y axis",
                        variable=self.fft_log_var,
                        command=self.plot_fft).pack(side='left')
        ttk.Separator(ctrl, orient='vertical').pack(side='left', fill='y', padx=4)
        ttk.Button(ctrl, text="Compute FFT",
                   command=self.run_fft_only).pack(side='left', padx=4)
        self.fig_fft, self.canvas_fft, axes = _make_plot_area(parent, figsize=(11, 5))
        self.ax_fft = axes[0]

    # ── Edge detection tab ───────────────────────────────────────────────────

    def _build_edge_tab(self, parent):
        vpane = ttk.PanedWindow(parent, orient='vertical')
        vpane.pack(fill='both', expand=True)

        plot_frame = ttk.Frame(vpane)
        vpane.add(plot_frame, weight=3)

        ctrl = ttk.Frame(plot_frame)
        ctrl.pack(fill='x', padx=4, pady=2)
        ttk.Label(ctrl, text="Event #:").pack(side='left')
        self.edge_ev_var = tk.IntVar(value=1)
        self.edge_ev_spin = ttk.Spinbox(ctrl, from_=1, to=1,
                                         textvariable=self.edge_ev_var, width=6,
                                         command=self.plot_edge_event)
        self.edge_ev_spin.pack(side='left', padx=2)
        ttk.Label(ctrl, text="Context (s):").pack(side='left')
        self.edge_ctx_var = tk.DoubleVar(value=5.0)
        ttk.Entry(ctrl, textvariable=self.edge_ctx_var, width=7).pack(side='left', padx=2)
        ttk.Button(ctrl, text="Show Event",    command=self.plot_edge_event).pack(side='left', padx=4)
        ttk.Button(ctrl, text="Show Overview", command=self.plot_edge_overview).pack(side='left', padx=2)
        ttk.Separator(ctrl, orient='vertical').pack(side='left', fill='y', padx=4)
        ttk.Button(ctrl, text="Run Edge Detection",
                   command=self.run_edge_only).pack(side='left', padx=4)

        self.fig_ed, self.canvas_ed, axes = _make_plot_area(plot_frame, figsize=(11, 4))
        self.ax_ed = axes[0]

        # Event table
        tbl_frame = ttk.LabelFrame(vpane, text="Detected Edge Events")
        vpane.add(tbl_frame, weight=1)
        cols = ('Event', 'Start (s)', 'End (s)', 'Amplitude (μV)', 'Width (s)')
        self.edge_tbl = ttk.Treeview(tbl_frame, columns=cols,
                                      show='headings', height=6)
        for col in cols:
            self.edge_tbl.heading(col, text=col)
            self.edge_tbl.column(col, width=130, anchor='center')
        ys = ttk.Scrollbar(tbl_frame, orient='vertical',
                            command=self.edge_tbl.yview)
        self.edge_tbl.configure(yscrollcommand=ys.set)
        self.edge_tbl.pack(side='left', fill='both', expand=True)
        ys.pack(side='right', fill='y')
        self.edge_tbl.bind('<<TreeviewSelect>>', self._on_edge_select)

    # ── File operations ──────────────────────────────────────────────────────

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open TDD File",
            filetypes=[("TDD files", "*.tdd"), ("All files", "*.*")],
            initialdir=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'),
        )
        if path:
            self.filepath = path
            self._load_file()

    def _reload_data(self):
        if self.filepath:
            self._load_file()

    def _load_file(self):
        def task():
            try:
                self._set_status("Parsing header…")
                self.header = parse_header(self.filepath)

                self._set_status("Loading data…")
                limit = self.load_minutes_var.get()
                max_s = int(limit * 60 * self.header.sample_rate) if limit > 0 else None

                self.data, self.total_samples = load_data(
                    self.filepath, self.header, n_samples=max_s)
                n = self.data.shape[0]
                self.time_axis = np.arange(n, dtype=np.float32) / self.header.sample_rate

                # Clear stale results
                self.rms_results.clear()
                self.edge_results.clear()
                self.fft_results.clear()

                self.root.after(0, self._on_data_loaded)
            except Exception as exc:
                self.root.after(0, lambda: messagebox.showerror("Load Error", str(exc)))
                self.root.after(0, lambda: self._set_status(f"Error: {exc}"))

        threading.Thread(target=task, daemon=True).start()

    def _on_data_loaded(self):
        h  = self.header
        n  = self.data.shape[0]
        sr = h.sample_rate
        dur = n / sr
        fname = os.path.basename(self.filepath)

        self.info_vars['File'].config(text=fname)
        self.info_vars['Channels'].config(text=str(h.channel_count))
        self.info_vars['Sample Rate'].config(text=f"{sr} Hz")
        self.info_vars['Duration'].config(
            text=f"{dur / 60:.1f} min  ({n} samples)")
        self.info_vars['Sample ID'].config(text=h.sample_id)

        # Build descrambled channel map: sorted by channel ID
        self.chan_map = descramble_channels(h.channel_ids)
        chan_labels = [f"Ch {m['cid']}" for m in self.chan_map]
        self.channel_combo['values'] = chan_labels
        self.channel_combo.current(0)

        # Sync overview spinbox to actual channel count
        self.ov_spin.config(to=h.channel_count)
        self.ov_n_chan_var.set(h.channel_count)

        self._set_status(
            f"Loaded: {fname} | {h.channel_count} ch | {sr} Hz | "
            f"{dur / 60:.1f} min ({n} samples)")
        self._populate_metadata_tab()
        self.plot_overview()

    # ── Channel helpers ──────────────────────────────────────────────────────

    def _combo_pos(self) -> int:
        """Current position in the (sorted) channel combo."""
        return max(0, self.channel_combo.current())

    def _ch_col(self) -> int:
        """Data-column index for the currently selected channel."""
        return self.chan_map[self._combo_pos()]['col']

    def _ch_id(self) -> int:
        """Channel ID for the currently selected channel."""
        return self.chan_map[self._combo_pos()]['cid']

    def _ch_data(self, col: int | None = None) -> np.ndarray:
        """Return the data for a data-column index (defaults to selected)."""
        if col is None:
            col = self._ch_col()
        return self.data[:, col]

    def _on_channel_changed(self, _=None):
        """Update the active tab when the channel selector changes.

        Tab 0 (Metadata)       – no action needed
        Tab 1 (Overview)       – replot
        Tab 2 (Single Channel) – replot
        Tab 3 (Edge Detection) – rerun edge detection then refresh
        Tab 4 (RMS Detection)  – rerun RMS then refresh
        Tab 5 (FFT)            – rerun FFT then refresh
        """
        if self.data is None:
            return
        tab = self.nb.index(self.nb.select())
        if tab == 1:
            self.plot_overview()
        elif tab == 2:
            self.plot_single_ch()
        elif tab == 3:
            self.run_edge_only()    # rerun + refresh
        elif tab == 4:
            self.run_rms_only()     # rerun + refresh
        elif tab == 5:
            self.run_fft_only()     # rerun + refresh

    # ── Analysis runners ─────────────────────────────────────────────────────

    def _require_data(self) -> bool:
        if self.data is None:
            messagebox.showwarning("No Data", "Open a TDD file first.")
            return False
        return True

    def run_all(self):
        if not self._require_data():
            return
        def task():
            self._set_status("Running RMS…")
            self.progress_var.set(10)
            self._do_rms()
            self._set_status("Running edge detection…")
            self.progress_var.set(40)
            self._do_edges()
            self._set_status("Computing FFT…")
            self.progress_var.set(70)
            self._do_fft()
            self.progress_var.set(100)
            self.root.after(0, self._refresh_all)
            self.root.after(0, lambda: self._set_status("Analysis complete."))
            self.root.after(800, lambda: self.progress_var.set(0))
        threading.Thread(target=task, daemon=True).start()

    def run_rms_only(self):
        if not self._require_data():
            return
        def task():
            self._set_status("Running RMS…")
            self._do_rms()
            self.root.after(0, self._refresh_rms_tab)
            self.root.after(0, lambda: self._set_status("RMS detection done."))
        threading.Thread(target=task, daemon=True).start()

    def run_edge_only(self):
        if not self._require_data():
            return
        def task():
            self._set_status("Running edge detection…")
            self._do_edges()
            self.root.after(0, self._refresh_edge_tab)
            self.root.after(0, lambda: self._set_status("Edge detection done."))
        threading.Thread(target=task, daemon=True).start()

    def run_fft_only(self):
        if not self._require_data():
            return
        def task():
            self._set_status("Computing FFT…")
            self._do_fft()
            self.root.after(0, self.plot_fft)
            self.root.after(0, lambda: self._set_status("FFT done."))
        threading.Thread(target=task, daemon=True).start()

    def _do_rms(self):
        col = self._ch_col()
        cid = self._ch_id()
        self.rms_results[cid] = detect_rms_noise(
            self._ch_data(col),
            self.header.sample_rate,
            window_seconds       = self.rms_window_var.get(),
            threshold_multiplier = self.rms_thresh_var.get(),
        )

    def _do_edges(self):
        col = self._ch_col()
        cid = self._ch_id()
        self.edge_results[cid] = detect_edges(
            self._ch_data(col),
            self.header.sample_rate,
            amplitude_threshold_uv = self.edge_amp_var.get(),
            min_width_seconds      = self.edge_min_width_var.get(),
            max_width_seconds      = self.edge_max_width_var.get(),
        )

    def _do_fft(self):
        col = self._ch_col()
        cid = self._ch_id()
        self.fft_results[cid] = compute_fft_chunks(
            self._ch_data(col),
            self.header.sample_rate,
            chunk_minutes = self.fft_chunk_var.get(),
            window_type   = self.fft_window_var.get(),
        )
        self.fft_chunk_idx = 0

    def _refresh_all(self):
        self.plot_overview()
        self._refresh_edge_tab()
        self._refresh_rms_tab()
        self.plot_fft()

    # ── Plot: Overview ───────────────────────────────────────────────────────

    def plot_overview(self):
        if self.data is None:
            return
        ax = self.ax_ov
        ax.clear()

        n_show   = min(self.ov_n_chan_var.get(), len(self.chan_map))
        sel_pos  = self._combo_pos()
        # Use the first n_show entries in the sorted chan_map
        show_map = self.chan_map[:n_show]

        offset_uv = self.ov_offset_var.get()       # μV between traces
        offset_mv = offset_uv / 1000.0             # mV between traces
        t         = self.time_axis
        colors    = cm.tab20(np.linspace(0, 1, max(n_show, 1)))

        # Reset interactive-click state (indices = combo positions of shown channels)
        self._ov_indices     = list(range(n_show))
        self._ov_lines       = []
        self._ov_offset_used = offset_mv   # stored in mV for click handler

        for plot_i, m in enumerate(show_map):
            cid   = m['cid']
            col   = m['col']
            ch_mv = self.data[:, col] / 1000.0      # μV → mV
            y_off = plot_i * offset_mv

            line, = ax.plot(t, ch_mv + y_off, color=colors[plot_i],
                            linewidth=0.5, alpha=0.85)
            self._ov_lines.append(line)

            # RMS noise regions
            if self.ov_rms_var.get() and cid in self.rms_results:
                res = self.rms_results[cid]
                frac = 1.0 / max(n_show, 1)
                for s, e, _ in res.noise_segments:
                    e_clipped = min(e, len(t) - 1)
                    ax.axvspan(t[s], t[e_clipped], alpha=0.18, color='red',
                               ymin=plot_i * frac, ymax=(plot_i + 1) * frac)

            # Edge events
            if self.ov_edge_var.get() and cid in self.edge_results:
                for ev in self.edge_results[cid]:
                    if ev.start_sample < len(t):
                        ax.axvline(t[ev.start_sample], color='red',
                                   linewidth=0.6, alpha=0.6)

        # Let matplotlib auto-scale the y-axis to the data range
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Amplitude (mV)")
        ax.tick_params(axis='y', labelsize=8, labelleft=True)
        ax.yaxis.set_label_position('left')
        ax.yaxis.tick_left()
        title = f"Channel Overview  —  {n_show} channels shown"
        if n_show > 1:
            title += "   (click trace = select, double-click = isolate)"
        ax.set_title(title)
        ax.grid(True, alpha=0.25)
        self.canvas_ov.draw_idle()

    def _on_overview_click(self, event):
        """Handle click on the overview plot: identify or isolate a trace."""
        # Guard: must be left button, inside the axes, with valid data coords
        if event.button != 1:
            return
        if event.inaxes != self.ax_ov:
            return
        if event.ydata is None or event.xdata is None:
            return
        if self.data is None or not self._ov_indices:
            return
        # Skip if the toolbar has an active mode (pan / zoom)
        toolbar = self.fig_ov.canvas.toolbar
        if toolbar is not None and toolbar.mode != '':
            return

        offset = self._ov_offset_used
        if offset <= 0:
            return

        # Find the nearest trace by y-coordinate
        trace_idx = int(round(event.ydata / offset))
        trace_idx = max(0, min(trace_idx, len(self._ov_indices) - 1))
        combo_pos = self._ov_indices[trace_idx]   # position in sorted combo
        cid       = self.chan_map[combo_pos]['cid']

        # Manual double-click detection using timestamp
        now = time.time() * 1000  # ms
        elapsed = now - self._ov_last_click_time
        self._ov_last_click_time = now

        if elapsed < self._ov_dblclick_ms:
            # ── Double-click: isolate / restore ──────────────────────────
            self._ov_last_click_time = 0  # reset so triple-click doesn't fire
            if len(self._ov_indices) == 1:
                # Already isolated — restore all channels
                self.ov_n_chan_var.set(len(self.chan_map))
            else:
                self.channel_combo.current(combo_pos)
                self.ov_n_chan_var.set(1)
            self.plot_overview()
        else:
            # ── Single-click: highlight and select ───────────────────────
            self.channel_combo.current(combo_pos)
            self._set_status(
                f"Selected Channel {cid}  "
                f"(trace {trace_idx + 1}/{len(self._ov_indices)})"
            )
            # Reset all lines to default, then highlight the clicked one
            for i, ln in enumerate(self._ov_lines):
                if i == trace_idx:
                    ln.set_linewidth(2.0)
                    ln.set_alpha(1.0)
                else:
                    ln.set_linewidth(0.5)
                    ln.set_alpha(0.35)
            self.canvas_ov.draw_idle()

    # ── Plot: FFT ────────────────────────────────────────────────────────────

    def plot_fft(self):
        if self.data is None:
            return
        ax  = self.ax_fft
        ax.clear()
        cid = self._ch_id()
        chunks = self.fft_results.get(cid)

        if not chunks:
            ax.text(0.5, 0.5,
                    "No FFT data.\nClick 'Compute FFT' or 'Run Analysis'.",
                    ha='center', va='center', transform=ax.transAxes, fontsize=11)
            self.canvas_fft.draw_idle()
            return

        idx   = max(0, min(self.fft_chunk_idx, len(chunks) - 1))
        chunk = chunks[idx]
        freqs = chunk['freqs']
        power = chunk['power_db']

        ax.plot(freqs, power, linewidth=0.9, color='steelblue')

        max_f = self.fft_maxf_var.get()
        ax.set_xlim(0, max_f if max_f > 0 else freqs[-1])
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("Power (dB)")
        ax.set_title(
            f"FFT Power Spectrum  —  Ch {cid}  |  "
            f"Chunk {idx + 1}/{len(chunks)}  "
            f"({chunk['start_time'] / 60:.1f}–{chunk['end_time'] / 60:.1f} min)"
        )
        ax.grid(True, alpha=0.25, which='both')

        self.fft_nav_var.set(f"{idx + 1} / {len(chunks)}")
        self.canvas_fft.draw_idle()

    def _fft_prev(self):
        self.fft_chunk_idx = max(0, self.fft_chunk_idx - 1)
        self.plot_fft()

    def _fft_next(self):
        cid    = self._ch_id()
        chunks = self.fft_results.get(cid, [])
        self.fft_chunk_idx = min(len(chunks) - 1, self.fft_chunk_idx + 1)
        self.plot_fft()

    # ── Plot: Edge detection ─────────────────────────────────────────────────

    def _refresh_edge_tab(self):
        cid    = self._ch_id()
        events = self.edge_results.get(cid, [])
        sr     = self.header.sample_rate

        # Rebuild table
        self.edge_tbl.delete(*self.edge_tbl.get_children())
        for i, ev in enumerate(events):
            self.edge_tbl.insert('', 'end', values=(
                i + 1,
                f"{ev.start_seconds(sr):.2f}",
                f"{ev.end_seconds(sr):.2f}",
                f"{ev.amplitude:.1f}",
                f"{ev.width_seconds(sr):.3f}",
            ))

        n = len(events)
        if n > 0:
            self.edge_ev_spin.config(to=n)
            self.edge_ev_var.set(1)
            self.plot_edge_event()
        else:
            self.plot_edge_overview()

    def plot_edge_event(self):
        if self.data is None:
            return
        cid    = self._ch_id()
        ci     = self._ch_col()
        events = self.edge_results.get(cid, [])
        ax     = self.ax_ed
        ax.clear()

        if not events:
            ax.text(0.5, 0.5,
                    "No edge events.\nRun 'Edge Detection' first.",
                    ha='center', va='center', transform=ax.transAxes, fontsize=11)
            self.canvas_ed.draw_idle()
            return

        ev_idx = max(0, min(self.edge_ev_var.get() - 1, len(events) - 1))
        ev     = events[ev_idx]
        sr     = self.header.sample_rate
        ctx    = int(self.edge_ctx_var.get() * sr)

        s = max(0, ev.start_sample - ctx)
        e = min(len(self.data), ev.end_sample + ctx)
        t  = self.time_axis[s:e]
        ch = self.data[s:e, ci] / 1000.0  # μV → mV

        ax.plot(t, ch, color='steelblue', linewidth=0.8)
        ax.axvspan(self.time_axis[ev.start_sample],
                   self.time_axis[min(ev.end_sample, len(self.time_axis) - 1)],
                   alpha=0.25, color='red')
        ax.axvline(self.time_axis[ev.start_sample],
                   color='red', linewidth=1.2, linestyle='--', label="Rise")
        ax.axvline(self.time_axis[min(ev.end_sample, len(self.time_axis) - 1)],
                   color='darkred', linewidth=1.2, linestyle='--', label="Fall")

        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Amplitude (mV)")
        ax.set_title(
            f"Edge Event {ev_idx + 1}/{len(events)}  —  Ch {cid}  |  "
            f"Amp={ev.amplitude / 1000.0:.3f} mV   Width={ev.width_seconds(sr):.3f} s"
        )
        ax.legend(loc='upper right', fontsize=8)
        ax.grid(True, alpha=0.25)
        self.canvas_ed.draw_idle()

    def plot_edge_overview(self):
        if self.data is None:
            return
        ci     = self._ch_col()
        cid    = self._ch_id()
        events = self.edge_results.get(cid, [])
        ax     = self.ax_ed
        ax.clear()

        t  = self.time_axis
        ch = self.data[:, ci] / 1000.0  # μV → mV
        ax.plot(t, ch, color='steelblue', linewidth=0.5, alpha=0.75)

        for ev in events:
            end_s = min(ev.end_sample, len(t) - 1)
            ax.axvspan(t[ev.start_sample], t[end_s],
                       alpha=0.3, color='red', linewidth=0)

        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Amplitude (mV)")
        ax.set_title(f"Ch {cid}  —  {len(events)} edge events detected")
        ax.grid(True, alpha=0.25)
        if events:
            ax.legend(handles=[Patch(color='red', alpha=0.35,
                                     label=f"{len(events)} events")],
                      loc='upper right', fontsize=8)
        self.canvas_ed.draw_idle()

    def _on_edge_select(self, _=None):
        sel = self.edge_tbl.selection()
        if sel:
            val = self.edge_tbl.item(sel[0])['values'][0]
            self.edge_ev_var.set(int(val))
            self.plot_edge_event()

    # ── Report ───────────────────────────────────────────────────────────────

    def generate_report(self):
        if self.data is None:
            messagebox.showwarning("No Data", "Load a file and run analysis first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Report",
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        h  = self.header
        sr = h.sample_rate
        n  = self.data.shape[0]

        lines = [
            "=" * 72,
            "  STEP NOISE DETECTION REPORT",
            f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 72,
            "",
            "FILE INFORMATION",
            "-" * 40,
            f"  File         : {os.path.basename(self.filepath)}",
            f"  Sample ID    : {h.sample_id}",
            f"  Operator     : {h.operator}",
            f"  Run ID       : {h.run_id}",
            f"  System ID    : {h.system_id}",
            f"  Channels     : {h.channel_count}",
            f"  Sample Rate  : {sr} Hz",
            f"  Duration     : {n / sr:.1f} s  ({n / sr / 60:.2f} min)",
            f"  Samples      : {n}",
            f"  File version : {h.file_version}",
            "",
        ]

        # RMS
        if self.rms_results:
            lines += ["RMS NOISE DETECTION", "-" * 40]
            for cid, res in self.rms_results.items():
                lines += [
                    f"  Channel {cid}:",
                    f"    Baseline RMS    : {res.baseline_uv:.3f} μV",
                    f"    Noise threshold : {res.threshold_uv:.3f} μV",
                    f"    Noise segments  : {len(res.noise_segments)}",
                    f"    Noise fraction  : {res.noise_fraction * 100:.1f} %",
                ]
                for k, (s, e, rms) in enumerate(res.noise_segments[:20]):
                    lines.append(
                        f"      [{s / sr:8.2f} s – {e / sr:8.2f} s]  "
                        f"RMS = {rms:.2f} μV"
                    )
                if len(res.noise_segments) > 20:
                    lines.append(
                        f"      … and {len(res.noise_segments) - 20} more segments")
            lines.append("")

        # Edge events
        if self.edge_results:
            lines += ["EDGE DETECTION", "-" * 40]
            total = sum(len(v) for v in self.edge_results.values())
            lines.append(f"  Total events detected : {total}")
            for cid, events in self.edge_results.items():
                lines.append(f"\n  Channel {cid}  —  {len(events)} events:")
                for i, ev in enumerate(events[:50]):
                    lines.append(
                        f"    #{i+1:3d}  t={ev.start_seconds(sr):8.2f} s  "
                        f"amp={ev.amplitude:8.1f} μV  "
                        f"width={ev.width_seconds(sr):.4f} s"
                    )
                if len(events) > 50:
                    lines.append(f"    … and {len(events) - 50} more events")
            lines.append("")

        lines += ["END OF REPORT", "=" * 72]

        with open(path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        messagebox.showinfo("Report Saved", f"Report saved:\n{path}")
        self._set_status(f"Report saved: {path}")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self.root.after(0, lambda: self.status_var.set(msg))
