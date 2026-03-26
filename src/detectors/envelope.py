"""
Envelope / Hug step-noise detector.
Ported from StepNoiseCalculator.m

Idea: compute upper and lower signal envelopes.  For each sample, measure
"hug" = distance to the nearest envelope, and "mid" = distance to the
overall mean.  If mean(hug) < mean(mid) the data clusters near the
envelopes (i.e. bimodal / step-like) rather than near the centre.
The ratio  hug / mid  quantifies the effect: values < 1 indicate step noise.
"""
import numpy as np
from scipy import signal as scipy_signal
from dataclasses import dataclass


@dataclass
class EnvelopeResult:
    upper:      np.ndarray   # upper envelope (same length as input)
    lower:      np.ndarray   # lower envelope
    hug:        np.ndarray   # per-sample distance to nearest envelope
    mid:        np.ndarray   # per-sample distance to mean
    mean_hug:   float
    mean_mid:   float
    ratio:      float        # mean_hug / mean_mid  (< 1 → step noise)
    signal_mean: float


def _peak_envelope(data: np.ndarray, peak_sep: int, kind: str) -> np.ndarray:
    """
    Compute an envelope by finding peaks (or troughs), then interpolating.
    peak_sep controls the minimum distance between peaks.
    kind: 'upper' or 'lower'.
    """
    if kind == 'upper':
        idx, _ = scipy_signal.find_peaks(data, distance=peak_sep)
    else:
        idx, _ = scipy_signal.find_peaks(-data, distance=peak_sep)

    if len(idx) < 2:
        # Not enough peaks — return flat line at max/min
        val = float(np.max(data) if kind == 'upper' else np.min(data))
        return np.full_like(data, val, dtype=np.float64)

    # Include the first and last sample so the interpolation covers the
    # full length without extrapolation.
    idx = np.concatenate(([0], idx, [len(data) - 1]))
    vals = data[idx].astype(np.float64)

    # Ensure boundary values don't create artefacts
    if kind == 'upper':
        vals[0]  = max(vals[0],  vals[1])
        vals[-1] = max(vals[-1], vals[-2])
    else:
        vals[0]  = min(vals[0],  vals[1])
        vals[-1] = min(vals[-1], vals[-2])

    x_full = np.arange(len(data))
    return np.interp(x_full, idx, vals)


def detect_envelope(channel_data: np.ndarray,
                    peak_separation: int = 50) -> EnvelopeResult:
    """
    Envelope / hug step-noise detector (from StepNoiseCalculator.m).

    Parameters
    ----------
    channel_data : 1-D array in any unit (μV or mV — result units match input).
    peak_separation : minimum sample distance between envelope peaks.
                      Corresponds to the MATLAB ``envelope(s, 50, 'peak')``
                      parameter.  Higher values produce smoother envelopes.

    Returns
    -------
    EnvelopeResult with upper/lower envelopes, per-sample hug & mid arrays,
    scalar means, ratio, and signal mean.
    """
    sig = channel_data.astype(np.float64)
    upper = _peak_envelope(sig, peak_separation, 'upper')
    lower = _peak_envelope(sig, peak_separation, 'lower')

    avg = float(np.mean(sig))

    d_top = np.abs(sig - upper)
    d_bot = np.abs(sig - lower)
    hug   = np.minimum(d_top, d_bot)
    mid   = np.abs(sig - avg)

    mean_hug = float(np.mean(hug))
    mean_mid = float(np.mean(mid))
    ratio    = mean_hug / mean_mid if mean_mid > 0 else float('inf')

    return EnvelopeResult(
        upper       = upper,
        lower       = lower,
        hug         = hug,
        mid         = mid,
        mean_hug    = mean_hug,
        mean_mid    = mean_mid,
        ratio       = ratio,
        signal_mean = avg,
    )
