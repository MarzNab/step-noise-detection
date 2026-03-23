"""
Step Noise Detection Algorithms
  - FFT power spectrum (5-min chunks, configurable window)
  - RMS auto-detection
  - Edge detection (rising then falling, configurable amplitude + width)
"""
import numpy as np
from scipy import signal as scipy_signal
from dataclasses import dataclass, field
from typing import List, Tuple


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class EdgeEvent:
    start_sample: int
    end_sample:   int
    amplitude:    float   # μV, height of the rising step
    width_samples: int    # samples between rise-end and fall-start

    def width_seconds(self, sample_rate: float) -> float:
        return self.width_samples / sample_rate

    def start_seconds(self, sample_rate: float) -> float:
        return self.start_sample / sample_rate

    def end_seconds(self, sample_rate: float) -> float:
        return self.end_sample / sample_rate


@dataclass
class RMSResult:
    baseline_uv:    float
    threshold_uv:   float
    noise_segments: List[Tuple[int, int, float]]  # (start, end, mean_rms)
    noise_fraction: float                          # 0–1


# ── RMS auto-detection ──────────────────────────────────────────────────────

def detect_rms_noise(channel_data: np.ndarray,
                     sample_rate: float,
                     window_seconds: float = 1.0,
                     threshold_multiplier: float = 3.0) -> RMSResult:
    """
    Compute a sliding-window RMS, estimate the quiet baseline as the 25th
    percentile, and flag segments that exceed threshold_multiplier × baseline.
    """
    win = max(1, int(window_seconds * sample_rate))
    sq  = channel_data.astype(np.float64) ** 2
    # Uniform convolution gives a sliding mean of x²
    rms = np.sqrt(np.convolve(sq, np.ones(win) / win, mode='same'))

    baseline  = float(np.percentile(rms, 25))
    threshold = threshold_multiplier * baseline

    noisy = rms > threshold
    segments: List[Tuple[int, int, float]] = []
    in_noise = False
    seg_start = 0

    for i, flag in enumerate(noisy):
        if flag and not in_noise:
            seg_start = i
            in_noise  = True
        elif not flag and in_noise:
            segments.append((seg_start, i, float(np.mean(rms[seg_start:i]))))
            in_noise = False
    if in_noise:
        segments.append((seg_start, len(noisy),
                         float(np.mean(rms[seg_start:]))))

    noise_samples   = sum(e - s for s, e, _ in segments)
    noise_fraction  = noise_samples / max(1, len(channel_data))

    return RMSResult(
        baseline_uv    = baseline,
        threshold_uv   = threshold,
        noise_segments = segments,
        noise_fraction = noise_fraction,
    )


# ── Edge detection ──────────────────────────────────────────────────────────

def detect_edges(channel_data: np.ndarray,
                 sample_rate: float,
                 amplitude_threshold_uv: float,
                 min_width_seconds: float,
                 max_width_seconds: float) -> List[EdgeEvent]:
    """
    Detect step-noise events: a rising edge followed by a falling edge whose
    plateau width falls within [min_width, max_width].

    amplitude_threshold_uv  – minimum step height to consider
    min/max_width_seconds   – acceptable plateau width (between rise and fall)
    """
    min_w = max(1, int(min_width_seconds * sample_rate))
    max_w = max(min_w + 1, int(max_width_seconds * sample_rate))

    # Savitzky-Golay smoothing to stabilise the derivative
    sg_len = min(max(5, int(0.05 * sample_rate) | 1), 101)
    if sg_len % 2 == 0:
        sg_len += 1
    try:
        smoothed = scipy_signal.savgol_filter(
            channel_data.astype(np.float64), sg_len, 2)
    except ValueError:
        smoothed = channel_data.astype(np.float64)

    diff      = np.diff(smoothed)
    half_thr  = amplitude_threshold_uv / 2.0
    n         = len(diff)
    events: List[EdgeEvent] = []
    i = 0

    while i < n - 1:
        # ── Look for the start of a positive (rising) edge ──────────────────
        if diff[i] > half_thr:
            rise_start = i
            while i < n and diff[i] > 0:
                i += 1
            rise_end = i
            rise_amp = float(smoothed[rise_end] - smoothed[rise_start])

            if rise_amp >= amplitude_threshold_uv:
                # ── Search for matching falling edge within width window ─────
                j = rise_end
                limit = min(rise_end + max_w + 1, n)
                while j < limit:
                    if diff[j] < -half_thr:
                        fall_start = j
                        while j < n and diff[j] < 0:
                            j += 1
                        fall_end  = j
                        fall_amp  = float(smoothed[fall_end] - smoothed[fall_start])
                        plateau_w = fall_start - rise_end

                        if (abs(fall_amp) >= amplitude_threshold_uv * 0.4
                                and min_w <= plateau_w <= max_w):
                            events.append(EdgeEvent(
                                start_sample  = rise_start,
                                end_sample    = fall_end,
                                amplitude     = rise_amp,
                                width_samples = plateau_w,
                            ))
                            i = fall_end
                        else:
                            # Falling edge found but doesn't qualify – skip
                            i = rise_end
                        break
                    j += 1
                else:
                    i = rise_end   # No falling edge in window
            # else: step too small, continue from rise_end
        else:
            i += 1

    return events


# ── FFT power spectrum ──────────────────────────────────────────────────────

WINDOW_FUNCTIONS = {
    'hann':        np.hanning,
    'hamming':     np.hamming,
    'blackman':    np.blackman,
    'rectangular': lambda n: np.ones(n),
    'flattop':     lambda n: scipy_signal.windows.flattop(n),
}


def compute_fft_chunks(channel_data: np.ndarray,
                       sample_rate: float,
                       chunk_minutes: float = 5.0,
                       window_type: str = 'hann') -> List[dict]:
    """
    Split channel_data into chunks of chunk_minutes each, compute the FFT
    power spectrum of each chunk, and return a list of result dicts:
        {freqs, power_db, start_sample, end_sample, start_time, end_time}

    Useful for spotting periodic noise peaks.
    """
    chunk_size = max(64, int(chunk_minutes * 60.0 * sample_rate))
    win_func   = WINDOW_FUNCTIONS.get(window_type, np.hanning)
    n          = len(channel_data)
    chunks     = []

    for start in range(0, n, chunk_size):
        end   = min(start + chunk_size, n)
        chunk = channel_data[start:end].astype(np.float64)
        if len(chunk) < 16:
            continue

        win      = win_func(len(chunk))
        windowed = chunk * win
        fft_out  = np.fft.rfft(windowed)
        freqs    = np.fft.rfftfreq(len(chunk), d=1.0 / sample_rate)
        power    = (np.abs(fft_out) ** 2) / np.sum(win ** 2)
        power_db = 10.0 * np.log10(power + 1e-20)

        chunks.append({
            'freqs':        freqs,
            'power_db':     power_db,
            'start_sample': start,
            'end_sample':   end,
            'start_time':   start / sample_rate,
            'end_time':     end   / sample_rate,
            'chunk_idx':    len(chunks),
        })

    return chunks
