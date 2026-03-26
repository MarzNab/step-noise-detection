"""FFT power spectrum analysis in configurable time chunks."""
import numpy as np
from scipy import signal as scipy_signal
from typing import List


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
