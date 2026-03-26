"""RMS auto-detection of noisy signal segments."""
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class RMSResult:
    baseline_uv:    float
    threshold_uv:   float
    noise_segments: List[Tuple[int, int, float]]  # (start, end, mean_rms)
    noise_fraction: float                          # 0–1


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
