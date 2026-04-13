const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat,
} = require("docx");

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
        { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "\u25E6", alignment: AlignmentType.LEFT,
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
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      children: [
        new Paragraph({ spacing: { before: 3000, after: 200 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: "Step Noise Finder", font: "Arial", bold: true, size: 52, color: "1A3668" }),
        ]}),
        new Paragraph({ spacing: { after: 100 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: "User Guide", font: "Arial", size: 36, color: "2E5090" }),
        ]}),
        new Paragraph({ spacing: { after: 100 }, alignment: AlignmentType.CENTER, children: [
          new TextRun({ text: "Version 1.0.0", font: "Arial", size: 28, color: "2E5090" }),
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
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({ children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: "Step Noise Finder \u2014 User Guide", font: "Arial", size: 18, color: "999999", italics: true })],
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
        p("The Step Noise Finder is a standalone desktop application that automatically scans all channels in one or more TDD baseline files and identifies regions containing periodic step noise. It is designed for Nabsys HD-Mapping instruments that produce multi-channel voltage data (typically 256 channels at 100 Hz)."),
        p("Step noise is a characteristic interference pattern where the signal abruptly rises, holds at an elevated level, then falls back in a repeating periodic pattern. The application detects step noise in the 4\u201320 Hz frequency range with configurable amplitude thresholds."),

        // ── 2. Getting Started ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("2. Getting Started")] }),
        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("2.1 Running the Application")] }),
        p("Launch the application by running:"),
        code("python src/step_finder.py"),
        p("Or use the standalone executable:"),
        code("StepNoiseFinder.exe"),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("2.2 System Requirements")] }),
        bullet("Python 3.11+ with NumPy, SciPy, and Matplotlib"),
        bullet("Or the standalone StepNoiseFinder.exe (no Python required)"),
        bullet("Windows 10/11"),
        bullet("Minimum 4 GB RAM (8 GB recommended for batch scanning)"),

        // ── 3. User Interface ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("3. User Interface")] }),
        p("The application window is divided into four areas:"),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.1 Menu Bar")] }),
        p("The File menu provides three commands:"),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2400, 1400, 5560],
          rows: [
            new TableRow({ children: [hdrCell("Menu Item", 2400), hdrCell("Shortcut", 1400), hdrCell("Description", 5560)] }),
            new TableRow({ children: [cell("Open TDD\u2026", 2400), cell("Ctrl+O", 1400), cell("Open a single TDD file for scanning. After loading, press Scan All Channels to analyze.", 5560)] }),
            new TableRow({ children: [cell("Scan Baselines\u2026", 2400), cell("Ctrl+Shift+O", 1400), cell("Open a file picker showing TDD files. Select one or more *Baseline*.tdd files to scan all of them in batch.", 5560)] }),
            new TableRow({ children: [cell("Exit", 2400), cell("", 1400), cell("Close the application.", 5560)] }),
          ],
        }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.2 Toolbar (Top Bar)")] }),
        p("The toolbar displays the loaded file information and detection parameters:"),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2000, 1200, 6160],
          rows: [
            new TableRow({ children: [hdrCell("Control", 2000), hdrCell("Default", 1200), hdrCell("Description", 6160)] }),
            new TableRow({ children: [cell("File", 2000), cell("\u2014", 1200), cell("Displays the loaded filename (truncated before the first underscore), channel count, sample rate, and duration.", 6160)] }),
            new TableRow({ children: [cell("Amp \u2265 (mV)", 2000), cell("100.0", 1200), cell("Minimum peak-to-peak amplitude of the bandpass-filtered signal (in millivolts) required to flag a window as containing step noise.", 6160)] }),
            new TableRow({ children: [cell("Freq (Hz)", 2000), cell("4 \u2013 20", 1200), cell("Frequency range for the step noise search. The bandpass filter and autocorrelation lag range are derived from these values.", 6160)] }),
            new TableRow({ children: [cell("Periodicity \u2265", 2000), cell("0.40", 1200), cell("Minimum autocorrelation score (0\u20131). Higher values require more regular periodicity. A value of 0.4 reliably separates step noise from random fluctuations.", 6160)] }),
            new TableRow({ children: [cell("Scan All Channels", 2000), cell("\u2014", 1200), cell("Scans all channels in the currently loaded single file. Enabled after Open TDD.", 6160)] }),
            new TableRow({ children: [cell("Scan Baselines\u2026", 2000), cell("\u2014", 1200), cell("Opens a file picker to select multiple baseline TDD files for batch scanning.", 6160)] }),
          ],
        }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.3 Progress Bar")] }),
        p("Displays scan progress showing the current channel and file being analyzed, along with a count of completed items out of the total."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.4 Results Table (Left Panel)")] }),
        p("After scanning, each row represents a contiguous time segment where step noise was detected. The table columns are:"),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2000, 7360],
          rows: [
            new TableRow({ children: [hdrCell("Column", 2000), hdrCell("Description", 7360)] }),
            new TableRow({ children: [cell("File", 2000), cell("Filename (truncated) of the source TDD file.", 7360)] }),
            new TableRow({ children: [cell("Channel", 2000), cell("Channel ID (physical channel number, sorted numerically).", 7360)] }),
            new TableRow({ children: [cell("Start (s)", 2000), cell("Start time of the step noise segment in seconds from the beginning of the file.", 7360)] }),
            new TableRow({ children: [cell("End (s)", 2000), cell("End time of the step noise segment.", 7360)] }),
            new TableRow({ children: [cell("Freq (Hz)", 2000), cell("Estimated step noise frequency, averaged across merged windows.", 7360)] }),
            new TableRow({ children: [cell("Amp (mV)", 2000), cell("Maximum peak-to-peak amplitude of the bandpass-filtered signal in the segment (millivolts).", 7360)] }),
            new TableRow({ children: [cell("Pre BL (mV)", 2000), cell("Pre-baseline: the highest raw signal value in the 10-second window immediately before the step noise starts.", 7360)] }),
            new TableRow({ children: [cell("Post BL (mV)", 2000), cell("Post-baseline: the highest raw signal value in the 10-second window immediately after the step noise ends. Shows \u2018\u2014\u2019 if the segment reaches the end of the file.", 7360)] }),
            new TableRow({ children: [cell("Periodicity", 2000), cell("Peak autocorrelation score (0\u20131). Higher values indicate more regular, periodic step noise.", 7360)] }),
          ],
        }),
        p("Click any row to view the waveform plots in the right panel."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.5 Plot Area (Right Panel)")] }),
        p("When a result row is selected, two plots are displayed:"),
        bullet("Raw Signal (top): The unfiltered channel voltage in millivolts. The detected step noise region is highlighted in red. Green and orange dashed lines show the pre-baseline and post-baseline values respectively."),
        bullet("Bandpass Filtered (bottom): The signal after bandpass filtering in the configured frequency range, showing the periodic step noise pattern clearly."),
        p("Both plots include 5 seconds of context before and after the detected segment. The matplotlib toolbar below the plots provides zoom, pan, and save functionality."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3.6 Status Bar")] }),
        p("The bottom status bar shows the current application state: file loading status, scan progress summary, or scan completion results (number of segments, channels, and files with detections)."),

        // ── 4. Detection Algorithm ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("4. Detection Algorithm")] }),
        p("The step noise detection uses a sliding-window approach combining bandpass filtering with autocorrelation analysis:"),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.1 Signal Conditioning")] }),
        p("The raw channel data (in microvolts) is passed through a 2nd-order zero-phase Butterworth bandpass filter. The passband is set slightly wider than the configured frequency range (1 Hz below the low cutoff, 5 Hz above the high cutoff) to avoid edge effects."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.2 Sliding Window Analysis")] }),
        p("The filtered signal is divided into overlapping windows:"),
        bullet("Window size: 10 seconds (1000 samples at 100 Hz)"),
        bullet("Overlap: 50% (windows advance by 5 seconds)"),
        p("For each window, two tests are applied:"),

        new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("Amplitude Gate")] }),
        p("The peak-to-peak amplitude of the filtered signal in the window is computed. If it is below the configured minimum (default 100 mV), the window is skipped. This eliminates quiet segments quickly."),

        new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun("Periodicity Check (Autocorrelation)")] }),
        p("Autocorrelation measures how similar a signal is to a time-shifted copy of itself. For a periodic signal like step noise, the signal at time t closely resembles the signal at time t + T, where T is the period of the repeating pattern. This self-similarity produces a strong peak in the autocorrelation function at lag T."),
        p("The algorithm computes the full normalized autocorrelation of the windowed signal, then examines only the lag range that corresponds to the target frequency band:"),
        bullet("Minimum lag = sample_rate / freq_hi \u2014 the shortest period to look for. At 100 Hz sample rate and 20 Hz upper limit, this is 100/20 = 5 samples (0.05 seconds)."),
        bullet("Maximum lag = sample_rate / freq_lo \u2014 the longest period to look for. At 100 Hz and 4 Hz lower limit, this is 100/4 = 25 samples (0.25 seconds)."),
        p("The autocorrelation is normalized so that lag 0 (perfect self-match) equals 1.0. The peak value found in the target lag range is the periodicity score:"),
        bullet("A score near 0 means the signal has no repeating pattern at that frequency \u2014 it is random noise."),
        bullet("A score of 0.4\u20130.6 indicates a moderately periodic signal \u2014 step noise is present but may be irregular or mixed with other noise."),
        bullet("A score above 0.7 indicates a very regular, strong periodic pattern \u2014 clean, consistent step noise."),
        p("The lag at which the peak occurs directly gives the step noise frequency: frequency = sample_rate / peak_lag. For example, a peak at lag 10 at 100 Hz sample rate gives 100/10 = 10 Hz step noise."),
        p("The default periodicity threshold of 0.4 was empirically chosen to reliably separate true periodic step noise from random high-amplitude fluctuations. Random noise rarely produces autocorrelation peaks above 0.3 in the 4\u201320 Hz lag range, while even moderate step noise typically exceeds 0.4."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.3 Merging")] }),
        p("Adjacent or overlapping flagged windows are merged into contiguous segments. During merging, the maximum amplitude and periodicity are preserved, and frequencies are averaged."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("4.4 Baseline Computation")] }),
        p("For each merged segment, the application computes:"),
        bullet("Pre-baseline: The maximum raw signal value in the 10-second window immediately before the segment starts."),
        bullet("Post-baseline: The maximum raw signal value in the 10-second window immediately after the segment ends (if data is available)."),
        p("These baselines help assess whether the step noise is causing a DC offset shift in the channel."),

        // ── 5. Typical Workflow ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("5. Typical Workflow")] }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("5.1 Single File Analysis")] }),
        numb(1, "Click File \u2192 Open TDD\u2026 or press Ctrl+O."),
        numb(2, "Select a TDD baseline file."),
        numb(3, "Adjust detection parameters if needed (amplitude, frequency range, periodicity)."),
        numb(4, "Click Scan All Channels."),
        numb(5, "Review results in the table. Click any row to view waveforms."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("5.2 Batch Analysis (Multiple Files)")] }),
        numb(1, "Click Scan Baselines\u2026 button or File \u2192 Scan Baselines\u2026"),
        numb(2, "Navigate to the folder containing baseline TDD files."),
        numb(3, "Select one or more *Baseline*.tdd files (use Ctrl+click or Ctrl+A)."),
        numb(4, "The scan starts automatically across all selected files and all channels."),
        numb(5, "Results from all files appear in the same table for comparison."),

        // ── 6. Parameter Tuning ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("6. Parameter Tuning Guide")] }),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2000, 2400, 2400, 2560],
          rows: [
            new TableRow({ children: [hdrCell("Parameter", 2000), hdrCell("Increase to\u2026", 2400), hdrCell("Decrease to\u2026", 2400), hdrCell("Note", 2560)] }),
            new TableRow({ children: [cell("Amp \u2265 (mV)", 2000), cell("Reduce false positives from low-amplitude noise", 2400), cell("Catch weaker step noise", 2400), cell("100 mV is typical for Nabsys instruments", 2560)] }),
            new TableRow({ children: [cell("Freq range", 2000), cell("Widen range to catch variable-frequency steps", 2400), cell("Narrow to target a known frequency", 2400), cell("4\u201320 Hz covers most observed step noise", 2560)] }),
            new TableRow({ children: [cell("Periodicity", 2000), cell("Require more regular patterns (fewer false positives)", 2400), cell("Detect intermittent or irregular step noise", 2400), cell("0.4 is a good balance; below 0.3 may produce false positives", 2560)] }),
          ],
        }),

        // ── 7. Technical Details ──
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("7. Technical Details")] }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("7.1 TDD File Format")] }),
        p("The application reads TDD v3.1 binary files produced by Nabsys HD-Mapping instruments. Key details:"),
        bullet("Magic number: 0x5342414E (NABS, little-endian)"),
        bullet("Data format: interleaved int16 samples, one per channel per time step"),
        bullet("Conversion: raw counts are multiplied by per-channel calibration factors to produce microvolts"),
        bullet("Typical files: 256 channels at 100 Hz, 30 minutes of data (~88 MB)"),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("7.2 Channel Ordering")] }),
        p("Channel IDs in TDD files are in hardware-scrambled order. The application uses a descrambling function to sort channels numerically (1, 2, 3, \u2026, 256) for display and analysis."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("7.3 Memory Management")] }),
        p("During batch scanning, the application caches loaded file data for plotting. Files without detections are evicted from the cache to conserve memory. Files with detections are retained so their waveforms can be viewed by clicking result rows."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("7.4 Threading")] }),
        p("All scanning runs in a background thread to keep the GUI responsive. Progress updates are dispatched to the main thread via tkinter\u2019s after() mechanism."),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("7.5 Dependencies")] }),
        bullet("Python 3.11+"),
        bullet("NumPy \u2265 1.24.0 \u2014 array operations"),
        bullet("SciPy \u2265 1.10.0 \u2014 Butterworth filter, zero-phase filtering"),
        bullet("Matplotlib \u2265 3.7.0 \u2014 embedded plots with tkinter backend"),
      ],
    },
  ],
});

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
function bullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
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

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("doc/Step Noise Finder - User Guide.docx", buffer);
  console.log("OK");
});
