"""Edge detection for step noise (rising edge → plateau → falling edge)."""
import numpy as np
from scipy import signal as scipy_signal
from dataclasses import dataclass
from typing import List


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
