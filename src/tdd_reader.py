"""
TDD File Format V3.1 Parser
Little-endian binary format with interleaved int16 channel data.
"""
import struct
import os
import numpy as np
from dataclasses import dataclass
from typing import List


@dataclass
class TDDHeader:
    magic: int
    file_type: int
    file_version: int
    channel_count: int
    file_number: int
    module_number: int
    run_start_time: float
    system_id: str
    software: str
    software_version: str
    system_controller_sw_version: str
    operator: str
    run_id: str
    protocol_name: str
    protocol_version: str
    settings_group: str
    settings_version: str
    module_model_number: str
    module_serial_number: str
    detector_type: str
    detector_lot_number: str
    detector_wafer_id: str
    detector_die_number: str
    reagent_lot: str
    sample_id: str
    scientist_name: str
    sample_type: str
    sample_description: str
    sample_method: str
    tag_size: str
    sample_rate: int
    factors: List[float]
    low_pass_filter: List[float]
    high_pass_filter: List[float]
    channel_ids: List[int]
    segment_length: int
    data_start_time: float
    data_offset: int  # byte offset where raw data begins


def _read_str(f) -> str:
    """Read a length-prefixed string (4-byte int32 length + UTF-8 bytes)."""
    raw = f.read(4)
    if len(raw) < 4:
        return ''
    length = struct.unpack('<i', raw)[0]
    if length <= 0:
        return ''
    return f.read(length).decode('utf-8', errors='replace')


def _read_tstmp(f) -> float:
    """Read a 16-byte timestamp (fractional + whole seconds from 1904 epoch)."""
    frac_bytes = f.read(8)
    whole_bytes = f.read(8)
    frac = struct.unpack('<Q', frac_bytes)[0]   # unsigned 64-bit
    whole = struct.unpack('<q', whole_bytes)[0]  # signed 64-bit
    return whole + frac / (2 ** 64)


def parse_header(filepath: str) -> TDDHeader:
    """Parse the TDD file header and return a TDDHeader dataclass."""
    with open(filepath, 'rb') as f:
        magic = struct.unpack('<i', f.read(4))[0]
        if magic != 0x5342414E:
            raise ValueError(f"Not a valid TDD file (bad magic: {magic:#010x})")

        file_type     = struct.unpack('<i', f.read(4))[0]
        file_version  = struct.unpack('<i', f.read(4))[0]
        channel_count = struct.unpack('<i', f.read(4))[0]
        file_number   = struct.unpack('<i', f.read(4))[0]
        module_number = struct.unpack('<i', f.read(4))[0]
        run_start_time = _read_tstmp(f)

        system_id                  = _read_str(f)
        software                   = _read_str(f)
        software_version           = _read_str(f)
        system_controller_sw_version = _read_str(f)
        operator                   = _read_str(f)
        run_id                     = _read_str(f)
        protocol_name              = _read_str(f)
        protocol_version           = _read_str(f)
        settings_group             = _read_str(f)
        settings_version           = _read_str(f)
        module_model_number        = _read_str(f)
        module_serial_number       = _read_str(f)
        detector_type              = _read_str(f)
        detector_lot_number        = _read_str(f)
        detector_wafer_id          = _read_str(f)
        detector_die_number        = _read_str(f)
        reagent_lot                = _read_str(f)
        sample_id                  = _read_str(f)
        scientist_name             = _read_str(f)
        sample_type                = _read_str(f)
        sample_description         = _read_str(f)
        sample_method              = _read_str(f)
        tag_size                   = _read_str(f)

        sample_rate = struct.unpack('<i', f.read(4))[0]

        n = channel_count
        factors         = list(struct.unpack(f'<{n}d', f.read(8 * n)))
        low_pass_filter = list(struct.unpack(f'<{n}d', f.read(8 * n)))
        high_pass_filter= list(struct.unpack(f'<{n}d', f.read(8 * n)))
        channel_ids     = list(struct.unpack(f'<{n}i', f.read(4 * n)))
        segment_length  = struct.unpack('<q', f.read(8))[0]
        data_start_time = _read_tstmp(f)
        data_offset     = f.tell()

    return TDDHeader(
        magic=magic, file_type=file_type, file_version=file_version,
        channel_count=channel_count, file_number=file_number,
        module_number=module_number, run_start_time=run_start_time,
        system_id=system_id, software=software,
        software_version=software_version,
        system_controller_sw_version=system_controller_sw_version,
        operator=operator, run_id=run_id,
        protocol_name=protocol_name, protocol_version=protocol_version,
        settings_group=settings_group, settings_version=settings_version,
        module_model_number=module_model_number,
        module_serial_number=module_serial_number,
        detector_type=detector_type, detector_lot_number=detector_lot_number,
        detector_wafer_id=detector_wafer_id, detector_die_number=detector_die_number,
        reagent_lot=reagent_lot, sample_id=sample_id,
        scientist_name=scientist_name, sample_type=sample_type,
        sample_description=sample_description, sample_method=sample_method,
        tag_size=tag_size, sample_rate=sample_rate,
        factors=factors, low_pass_filter=low_pass_filter,
        high_pass_filter=high_pass_filter, channel_ids=channel_ids,
        segment_length=segment_length, data_start_time=data_start_time,
        data_offset=data_offset,
    )


def get_total_samples(filepath: str, header: TDDHeader) -> int:
    """Return total number of samples available in the file."""
    file_size = os.path.getsize(filepath)
    return (file_size - header.data_offset) // (header.channel_count * 2)


def load_data(filepath: str, header: TDDHeader,
              start_sample: int = 0, n_samples: int = None) -> tuple:
    """
    Load a range of samples from the TDD file.

    Returns:
        (data, total_samples)
        data: float32 array of shape (n_samples, n_channels) in microvolts
        total_samples: total samples in file
    """
    n_channels = header.channel_count
    total_samples = get_total_samples(filepath, header)

    if n_samples is None:
        n_samples = total_samples - start_sample
    n_samples = min(n_samples, total_samples - start_sample)

    byte_offset = header.data_offset + start_sample * n_channels * 2
    byte_count  = n_samples * n_channels * 2

    with open(filepath, 'rb') as f:
        f.seek(byte_offset)
        raw = np.frombuffer(f.read(byte_count), dtype='<i2')

    raw = raw[: n_samples * n_channels].reshape(n_samples, n_channels)
    factors = np.array(header.factors, dtype=np.float32)
    data = raw.astype(np.float32) * factors
    return data, total_samples


# ── Channel ordering helpers ────────────────────────────────────────────────

def channel_seq_index(channel_id: int, n_channels: int = 256) -> int:
    """
    Map a physical channel ID to its sequential position in the serpentine
    ordering: 1, 3, 5, …, n-1, n, n-2, …, 4, 2
    """
    half = n_channels // 2
    if channel_id % 2 == 1:          # odd  → first half, ascending
        return (channel_id - 1) // 2
    else:                             # even → second half, descending from n
        return half + (n_channels - channel_id) // 2


def descramble_channels(channel_ids: List[int]) -> List[dict]:
    """
    Return a list sorted by channel ID, each element mapping a sorted
    display position to the raw data-column index.

    Returns:
        [{'cid': 1, 'col': 0}, {'cid': 2, 'col': 47}, ...]
        where 'col' is the index into the interleaved data array (i.e. the
        position of that channel_id in the original channel_ids list).
    """
    mapping = [{'cid': cid, 'col': col} for col, cid in enumerate(channel_ids)]
    mapping.sort(key=lambda m: m['cid'])
    return mapping


def get_close_channel_ids(target_cid: int, channel_ids: List[int],
                           n_neighbors: int = 5,
                           n_channels: int = 256) -> List[int]:
    """
    Return channel IDs within ±n_neighbors positions of target_cid in the
    serpentine ordering.  Result is sorted by sequence position.
    """
    seq = {cid: channel_seq_index(cid, n_channels) for cid in channel_ids}
    target_idx = seq.get(target_cid)
    if target_idx is None:
        return []
    return sorted(
        [cid for cid, idx in seq.items()
         if cid != target_cid and abs(idx - target_idx) <= n_neighbors],
        key=lambda c: seq[c]
    )
