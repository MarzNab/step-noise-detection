const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat,
} = require("docx");

const VERSION = "1.2.0";

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

function hdrCell(text, width) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill: "1A3668", type: ShadingType.CLEAR },
    margins: cellMargins,
    children: [new Paragraph({ children: [new TextRun({ text, bold: true, font: "Arial", size: 20, color: "FFFFFF" })] })],
  });
}
function cell(text, width) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    margins: cellMargins,
    children: [new Paragraph({ children: [new TextRun({ text, font: "Arial", size: 20 })] })],
  });
}

function p(text) {
  return new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text, font: "Arial", size: 22 })] });
}
function code(text) {
  return new Paragraph({
    spacing: { after: 120 },
    indent: { left: 360 },
    children: [new TextRun({ text, font: "Consolas", size: 20 })],
  });
}
function bullet(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 60 },
    children: [new TextRun({ text, font: "Arial", size: 22 })],
  });
}
function numb(n, text) {
  return new Paragraph({
    numbering: { reference: "numbers", level: 0 },
    spacing: { after: 60 },
    children: [new TextRun({ text, font: "Arial", size: 22 })],
  });
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: "1A3668" },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: "2E5090" },
        paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: "Arial", color: "333333" },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
      ]},
      { reference: "numbers", levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      ]},
    ],
  },
  sections: [
    // ── Title page ──
    {
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      children: [
        new Paragraph({ spacing: { before: 3000, after: 200 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: "Step Noise Finder", font: "Arial", bold: true, size: 52, color: "1A3668" }),
        ]}),
        new Paragraph({ spacing: { after: 100 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: "User Guide", font: "Arial", size: 36, color: "2E5090" }),
        ]}),
        new Paragraph({ spacing: { after: 100 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: `Version ${VERSION}`, font: "Arial", size: 28, color: "2E5090" }),
        ]}),
        new Paragraph({
          spacing: { after: 600 }, alignment: AlignmentType.CENTER,
          border: { bottom: { style: BorderStyle.SINGLE, color: "2E5090", size: 6, space: 8 } },
          children: [
            new TextRun({ text: "Automated Step Noise Detection for TDD Baseline Files", font: "Arial", italics: true, size: 24, color: "666666" }),
          ],
        }),
        new Paragraph({ spacing: { after: 80 } }),
        new Paragraph({ spacing: { after: 100 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: "Nabsys HD-Mapping TDD File Analysis", font: "Arial", size: 22, color: "333333" }),
        ]}),
        new Paragraph({ spacing: { after: 100 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: "Python / Tkinter / Matplotlib / SciPy", font: "Arial", size: 22, color: "333333" }),
        ]}),
      ],
    },

    // ── Content ──
    {
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      headers: {
        default: new Header({ children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: `Step Noise Finder — User Guide  v${VERSION}`, font: "Arial", size: 18, color: "999999", italics: true })],
        })] }),
      },
      footers: {
        default: new Footer({ children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Page ", font: "Arial", size: 18, color: "999999" }), new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: "999999" })],
        })] }),
      },
      children: [
        // ── 1. Introduction ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("1. Introduction")] }),
        p("The Step Noise Finder is a standalone desktop application that automatically scans every channel of one or more TDD baseline files and identifies regions containing step noise. It is designed for Nabsys HD-Mapping instruments that produce multi-channel voltage data (typically 256 channels)."),
        p("Step noise is a characteristic interference pattern where the signal abruptly rises, holds at an elevated level, then falls back, repeating in a periodic pattern. The application detects step noise in the 4–20 Hz frequency range with configurable amplitude thresholds, and offers a separate envelope mode for catching non-periodic bursts of activity in the same band."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(`1.1 What's New in ${VERSION}`)] }),
        bullet("Envelope-mode detection — catches bursty events that aren't strictly periodic."),
        bullet("Cross-file merging — a step-noise event that spans several consecutive TDD files is reported as a single event."),
        bullet("Recursive Scan All Runs — point at a top-level data root and the app walks the tree, finding every folder containing *Baseline*.tdd files."),
        bullet("Channel-level parallelism — scans use all CPU cores; combined with FFT autocorrelation this is roughly 10–30× faster than the previous serial implementation."),
        bullet("Sortable result columns and a per-folder Sherlock-compatible JSON report alongside the existing text report."),
        bullet("Saved settings — the toolbar parameters persist between runs in step_noise_finder_config.json."),
        bullet("8 GB bounded LRU cache — large batch scans no longer crash from memory pressure; files are reloaded transparently if you click a result whose data was evicted."),

        // ── 2. Getting Started ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("2. Getting Started")] }),
        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("2.1 Running the Application")] }),
        p("From a checked-out source tree using the project venv:"),
        code(".venv\\Scripts\\python.exe src\\step_finder.py"),
        p("Or use the standalone executable build:"),
        code("StepNoiseFinder.exe"),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("2.2 System Requirements")] }),
        bullet("Python 3.13 with NumPy, SciPy, and Matplotlib (or the standalone EXE — no Python required)"),
        bullet("Optional: the sherlock package (Nabsys-Organization/sherlock on GitHub) for JSON RunResults output. Without it the .json sibling reports are silently skipped; the .txt reports are always produced."),
        bullet("Windows 10/11"),
        bullet("8 GB RAM recommended for large batch scans (the app self-limits its file cache to 8 GB)"),

        // ── 3. User Interface ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("3. User Interface")] }),
        p("The window has four areas: a menu bar, a parameter toolbar, a results table on the left, and a plot panel on the right. A status line at the bottom shows progress and report-write notifications."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.1 File Menu")] }),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2400, 1400, 5560],
          rows: [
            new TableRow({ children: [hdrCell("Menu Item", 2400), hdrCell("Shortcut", 1400), hdrCell("Description", 5560)] }),
            new TableRow({ children: [cell("Open TDD…", 2400), cell("Ctrl+O", 1400), cell("Open one TDD file. Press Scan All Channels afterward to analyze it.", 5560)] }),
            new TableRow({ children: [cell("Scan Baselines…", 2400), cell("Ctrl+Shift+O", 1400), cell("Custom folder picker showing the *Baseline*.tdd files in the chosen folder. Scans every Baseline TDD in that folder.", 5560)] }),
            new TableRow({ children: [cell("Scan Files…", 2400), cell("", 1400), cell("Pick individual TDD files (any *.tdd) and scan only those.", 5560)] }),
            new TableRow({ children: [cell("Scan Folders…", 2400), cell("", 1400), cell("Add multiple folders to a queue (one at a time, with previews) and scan them in batch. Each folder produces its own report.", 5560)] }),
            new TableRow({ children: [cell("Scan All Runs…", 2400), cell("", 1400), cell("Pick a single root directory; the app recursively finds every subfolder containing *Baseline*.tdd files. A preview dialog lets you remove rows before scanning.", 5560)] }),
            new TableRow({ children: [cell("Exit", 2400), cell("", 1400), cell("Close the application. Settings are saved on every parameter change, so nothing is lost.", 5560)] }),
          ],
        }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.2 Toolbar")] }),
        p("All toolbar values persist between sessions (saved to step_noise_finder_config.json next to the app) and are applied when you click Scan All Channels or any of the menu commands."),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2200, 1200, 5960],
          rows: [
            new TableRow({ children: [hdrCell("Control", 2200), hdrCell("Default", 1200), hdrCell("Description", 5960)] }),
            new TableRow({ children: [cell("File", 2200), cell("—", 1200), cell("Loaded file or batch label (filename truncated before the first underscore for readability).", 5960)] }),
            new TableRow({ children: [cell("Amp ≥ (mV)", 2200), cell("100", 1200), cell("Minimum peak-to-peak amplitude of the bandpass signal in a 10 s window for the window to qualify.", 5960)] }),
            new TableRow({ children: [cell("Freq (Hz)", 2200), cell("4 – 20", 1200), cell("Frequency band of interest. Sets the bandpass filter and the autocorrelation lag-search range.", 5960)] }),
            new TableRow({ children: [cell("Periodicity ≥", 2200), cell("0.20", 1200), cell("Minimum normalized autocorrelation peak (0–1) in periodic mode. Ignored in envelope mode.", 5960)] }),
            new TableRow({ children: [cell("Gap (s)", 2200), cell("30", 1200), cell("Maximum gap between qualifying windows that will still be merged into one event. Bump above ~180 s to merge across consecutive files.", 5960)] }),
            new TableRow({ children: [cell("Envelope mode", 2200), cell("off", 1200), cell("When checked, the autocorrelation gates are bypassed. Detection only requires bandpass amplitude and ≥10 peaks per 10 s window.", 5960)] }),
            new TableRow({ children: [cell("Scan All Channels", 2200), cell("—", 1200), cell("Run detection. After the first scan, this button stays enabled — tweak parameters and click again to rescan the same target.", 5960)] }),
            new TableRow({ children: [cell("Scan Baselines…", 2200), cell("—", 1200), cell("Same as the menu item; opens the folder picker.", 5960)] }),
            new TableRow({ children: [cell("Full file (top plot)", 2200), cell("off", 1200), cell("When checked, the raw-signal plot shows the entire TDD file with the detected segment highlighted (instead of zooming on the event).", 5960)] }),
          ],
        }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.3 Progress and Status")] }),
        bullet("Analyzing indicator — a red, blinking \"● Analyzing…\" appears next to the progress bar while a scan is running."),
        bullet("Progress bar — fills as channels finish; the label shows the latest channel that completed (channel completion order is non-deterministic because of parallelism)."),
        bullet("Status line — announces report writes (\"Report written: <name>\") and final totals."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.4 Results Table")] }),
        p("After scanning, each row is a contiguous segment of step noise on a particular channel. Click a row to plot it; click any column header to sort by that column (an arrow indicator in the header shows current direction)."),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2000, 7360],
          rows: [
            new TableRow({ children: [hdrCell("Column", 2000), hdrCell("Description", 7360)] }),
            new TableRow({ children: [cell("File", 2000), cell("Filename of the source TDD (truncated). For cross-file events this is the first file in the merged span.", 7360)] }),
            new TableRow({ children: [cell("Channel", 2000), cell("Hardware channel ID (1-based, sorted numerically).", 7360)] }),
            new TableRow({ children: [cell("Start (s)", 2000), cell("Start of the segment, measured from the beginning of the source file.", 7360)] }),
            new TableRow({ children: [cell("End (s)", 2000), cell("End of the segment. For cross-file events this can exceed the source file's duration — the surplus represents time in subsequent files.", 7360)] }),
            new TableRow({ children: [cell("Freq (Hz)", 2000), cell("Periodic mode: autocorrelation peak frequency. Envelope mode: peaks per second.", 7360)] }),
            new TableRow({ children: [cell("Amp (mV)", 2000), cell("Maximum peak-to-peak of the filtered signal across the merged sub-windows of the segment.", 7360)] }),
            new TableRow({ children: [cell("Pre BL (mV)", 2000), cell("Pre-baseline: maximum raw value in the 10-second window immediately before the segment.", 7360)] }),
            new TableRow({ children: [cell("Post BL (mV)", 2000), cell("Post-baseline: maximum raw value in the 10-second window immediately after the segment. Shows \"—\" if the segment touches the end of the file.", 7360)] }),
            new TableRow({ children: [cell("Periodicity", 2000), cell("Highest autocorrelation score (0–1) seen across the segment. 0.0 in envelope mode (not measured).", 7360)] }),
          ],
        }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.5 Plot Panel")] }),
        bullet("Top — raw signal in millivolts. Detected region highlighted red. Pre/post baseline horizontal dashes shown. Toggle Full file (top plot) to see the whole file."),
        bullet("Bottom — the same channel through the bandpass filter, focused on the event window so you can see the periodic structure (or burst envelope in envelope mode)."),
        bullet("Both plots include 5 s of context before and after the event by default. The matplotlib toolbar provides zoom, pan, and save."),

        // ── 4. Detection Algorithm ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("4. Detection Algorithm")] }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.1 Signal Conditioning")] }),
        p("Each channel is filtered with a 2nd-order zero-phase Butterworth bandpass. The passband is wider than the user-selected range — 1 Hz below the low cutoff and 5 Hz above the high cutoff — so the rolloff edges don't clip events near the band boundaries. Filter coefficients are designed once per file and shared across all channel threads."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.2 Sliding Window")] }),
        bullet("Window length: 10 seconds."),
        bullet("Step: 5 seconds (50 % overlap)."),
        p("Each window passes through a cascade of gates. A window must clear every gate to qualify."),

        new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("Amplitude gate")] }),
        p("Peak-to-peak of the filtered window must exceed Amp ≥ (mV)."),

        new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("Periodic mode (default)")] }),
        bullet("Autocorrelation periodicity gate — the FFT-based normalized autocorrelation must have a peak ≥ Periodicity ≥ inside the lag range that corresponds to the chosen frequency band (lag = sample_rate / freq)."),
        bullet("Second-harmonic gate — a true periodic signal repeats at multiples of the fundamental lag, so the autocorrelation at 2× the peak lag must be ≥ 0.05. A single transient pulse passes the autocorrelation peak but fails this check."),
        bullet("Peak-count gate — the count of positive peaks above 20 % of the segment's pp must be at least 30 % of the count expected at the detected frequency. This rejects single spike + ringdown patterns that fool autocorrelation."),
        bullet("Frequency = sample_rate / peak-lag."),

        new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("Envelope mode (toggle)")] }),
        bullet("Skips the autocorrelation and second-harmonic gates entirely."),
        bullet("Counts positive peaks (above 20 % of pp) in the window."),
        bullet("Window passes if there are at least 10 peaks AND the implied frequency (peaks / 10 s) lies inside the user's frequency band."),
        bullet("Frequency = peaks / 10 s. Periodicity is reported as 0.0 because it is not measured."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.3 Merging and Boundary Refinement")] }),
        bullet("Adjacent qualifying windows within Gap (s) are merged into one segment. Maximum amplitude and periodicity are kept; frequencies are averaged."),
        bullet("After merging, the segment edges are refined: the algorithm walks outward from each edge in 2-second sub-windows and extends the segment as long as the local pp is at least 30 % of the segment's measured pp. Up to 3 consecutive quiet sub-windows (≈6 s) are tolerated before stopping, so brief envelope dips don't truncate events."),
        bullet("Refinement may cause initially separate segments to overlap, so a second merge pass is run afterward."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.4 Cross-file Merging")] }),
        p("In a folder scan, after every file in a folder has been processed, the per-file segments are projected onto wall-clock time using each TDD file's DataStartTime. Same-channel segments whose wall-clock gap is within the user's Gap (s) value are merged into a single event. The merged event is reported against the first contributing file, with start/end times measured from that file's start (so end may exceed that file's duration when the event spans into later files)."),
        p("To merge across files that are recorded ~3 minutes apart, set Gap (s) to roughly the file-to-file gap (e.g. 200) before scanning."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.5 Baselines")] }),
        bullet("Pre-baseline — the maximum raw value in the 10-second window immediately before the segment."),
        bullet("Post-baseline — the maximum raw value in the 10-second window immediately after the segment, when data is available; otherwise blank."),

        // ── 5. Workflow ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("5. Typical Workflow")] }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("5.1 Single file")] }),
        numb(1, "File → Open TDD… (or Ctrl+O)."),
        numb(2, "Adjust Amp / Freq / Periodicity / Gap if needed. Try Envelope mode for non-periodic bursts."),
        numb(3, "Click Scan All Channels."),
        numb(4, "Sort columns to find the strongest events. Click any row to inspect."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("5.2 One folder")] }),
        numb(1, "File → Scan Baselines…"),
        numb(2, "Browse to the folder with *Baseline*.tdd files. The picker shows the matching files for confirmation."),
        numb(3, "Click OK. Scan starts immediately and writes a report to results/ when it finishes."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("5.3 Many folders")] }),
        numb(1, "File → Scan Folders… to add folders one at a time, or File → Scan All Runs… to pick a root and let the app discover every Baseline-containing folder under it."),
        numb(2, "Inspect the discovered runs in the preview dialog. Remove any you don't want."),
        numb(3, "Scan. One report is written per source folder, named after the run and the time of the scan."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("5.4 Re-scanning with new parameters")] }),
        p("After any scan, the Scan All Channels button stays enabled. Tweak the toolbar parameters and click again — the same files are rescanned with the new settings. There is no need to re-pick the folder."),

        // ── 6. Output ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("6. Output Files")] }),
        p("Reports are written under the project's results/ directory (created if it does not exist). One pair of files is produced per source folder:"),
        bullet("<run-name>_<timestamp>.txt — a human-readable text report listing every detected segment with its file, channel, start/end, frequency, amplitude, baselines, and periodicity. The header records the parameters used for the scan."),
        bullet("<run-name>_<timestamp>.json — a Sherlock-compatible RunResults JSON, suitable for downstream analysis. Run-level metadata (run_id, wafer_id, die_number, lot_number, instrument_id) is pulled from the TDD header. Aggressors are keyed by channel, then by wall-clock ISO timestamp of the event's first sample."),
        p("The run name is derived from the first four dash-separated parts of the TDD filename (e.g. HAK04-008B-02L64869w15-205B16a). The File column inside the .txt report shows only the 14-digit datetime code from each filename."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("6.1 Settings file")] }),
        p("Toolbar parameters are stored in step_noise_finder_config.json next to the app. It is rewritten every time you change a value. To reset to defaults, delete the file."),

        // ── 7. Parameter Tuning ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("7. Parameter Tuning")] }),
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2000, 2400, 2400, 2560],
          rows: [
            new TableRow({ children: [hdrCell("Parameter", 2000), hdrCell("Increase to…", 2400), hdrCell("Decrease to…", 2400), hdrCell("Note", 2560)] }),
            new TableRow({ children: [cell("Amp ≥ (mV)", 2000), cell("Reduce false positives from low-amplitude noise", 2400), cell("Catch weaker step noise", 2400), cell("100 mV is typical for Nabsys instruments; try 50 mV if events look clean but aren't reported.", 2560)] }),
            new TableRow({ children: [cell("Freq range", 2000), cell("Widen to catch variable-frequency steps", 2400), cell("Narrow to target a known frequency", 2400), cell("4–20 Hz covers most observed step noise.", 2560)] }),
            new TableRow({ children: [cell("Periodicity", 2000), cell("Demand cleaner periodic signals (fewer false positives)", 2400), cell("Catch irregular or amplitude-modulated events", 2400), cell("0.20 default is fairly permissive; bump to 0.4+ for strict.", 2560)] }),
            new TableRow({ children: [cell("Gap (s)", 2000), cell("Merge events across long quiet stretches or across files", 2400), cell("Keep events more granular", 2400), cell("Set to ~200 to merge across 30-min files recorded ≈3 min apart.", 2560)] }),
            new TableRow({ children: [cell("Envelope mode", 2000), cell("(checkbox — enable for bursty / non-periodic events)", 2400), cell("(uncheck for strict periodic detection)", 2400), cell("Best for ragged spike trains in the band; reports peaks/sec as the frequency.", 2560)] }),
          ],
        }),

        // ── 8. Technical Details ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("8. Technical Details")] }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("8.1 TDD format")] }),
        bullet("Magic 0x5342414E (\"NABS\", little-endian)."),
        bullet("Per-channel int16 samples interleaved across time."),
        bullet("Per-channel calibration factors applied during load to convert raw counts to microvolts."),
        bullet("Header fields used by reports include RunID, DetectorWaferID, DetectorDieNumber, DetectorLotNumber, SystemID, and DataStartTime."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("8.2 Channel ordering")] }),
        p("Channels are recorded in hardware-scrambled order. The reader sorts them numerically (1–2…256) for display and analysis."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("8.3 Performance")] }),
        bullet("FFT autocorrelation — scipy.fft.rfft/irfft replaces direct convolution; ~1700× faster on the inner autocorrelation loop than np.correlate, with float-epsilon-equivalent results."),
        bullet("Channel-level parallelism — ThreadPoolExecutor with min(cpu_count, 8) workers. NumPy/SciPy release the GIL, so Python threads give real parallelism here."),
        bullet("LRU file cache — bounded by total waveform bytes (8 GB). Older files are evicted automatically as new ones load. If the user clicks a row whose file was evicted, the file is reloaded transparently to render the plot."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("8.4 Threading and Tk safety")] }),
        p("All scanning runs in background threads. Progress and table updates dispatch back to the Tk main thread via root.after(0, …), the only thread-safe Tk update path."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("8.5 Dependencies")] }),
        bullet("Python 3.13 (the installed venv version)."),
        bullet("numpy, scipy, matplotlib."),
        bullet("Optional: sherlock (Nabsys-Organization/sherlock). When installed, RunResults JSON output is enabled."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("8.6 Source layout")] }),
        bullet("src/step_finder.py — main GUI and detection."),
        bullet("src/tdd_reader.py — TDD v3.1 binary parser."),
        bullet("src/plot_results.py — helper to plot channel-vs-step-noise across all results/*.json."),
        bullet("doc/gen_finder_guide.js — source for this user guide; run with node to regenerate the docx."),
      ],
    },
  ],
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("doc/Step Noise Finder - User Guide.docx", buffer);
  console.log("OK");
});
