"""
Detection algorithm package.

Each detector lives in its own module:
  - detectors.rms       – RMS auto-detection
  - detectors.edge      – Edge detection (rising/falling step pairs)
  - detectors.envelope  – Envelope/hug detector (from StepNoiseCalculator.m)
  - detectors.fft       – FFT power spectrum analysis
"""
from .rms      import detect_rms_noise, RMSResult
from .edge     import detect_edges, EdgeEvent
from .fft      import compute_fft_chunks
from .envelope import detect_envelope, EnvelopeResult

__all__ = [
    'detect_rms_noise', 'RMSResult',
    'detect_edges', 'EdgeEvent',
    'compute_fft_chunks',
    'detect_envelope', 'EnvelopeResult',
]
