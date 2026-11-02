"""Decode EDF and EDF+ files. See https://www.edfplus.info/specs/."""

import struct
from dataclasses import dataclass
from datetime import datetime
from itertools import accumulate
from typing import BinaryIO

# Field widths of each signal in the header, in header order.
SIGNAL_FIELDS = (16, 80, 8, 8, 8, 8, 8, 80, 8, 32)


@dataclass(frozen=True)
class Signal:
    label: str
    physical_min: float
    physical_max: float
    digital_min: int
    digital_max: int
    samples: int  # per record

    @property
    def gain(self) -> float:
        return (self.physical_max - self.physical_min) / (
            self.digital_max - self.digital_min
        )

    @property
    def offset(self) -> float:
        return self.physical_min - self.digital_min * self.gain


@dataclass(frozen=True)
class Header:
    start: datetime  # device local time
    size: int
    records: int  # as the header gives it; can be stale while the device writes
    duration: float  # seconds per record
    signals: tuple[Signal, ...]

    @property
    def record_size(self) -> int:
        return 2 * sum(signal.samples for signal in self.signals)


@dataclass(frozen=True)
class Annotation:
    onset: float  # seconds after the file start
    duration: float | None
    text: str


def read_header(file: BinaryIO) -> Header:
    fixed = _read(file, 256)
    count = int(fixed[252:256])
    rest = _read(file, 256 * count)
    columns = []
    position = 0
    for width in SIGNAL_FIELDS:
        columns.append(
            [
                rest[position + i * width : position + (i + 1) * width]
                .decode("ascii")
                .strip()
                for i in range(count)
            ]
        )
        position += width * count
    labels, _, _, pmin, pmax, dmin, dmax, _, samples, _ = columns
    header = Header(
        start=datetime.strptime(fixed[168:184].decode("ascii"), "%d.%m.%y%H.%M.%S"),
        size=int(fixed[184:192]),
        records=int(fixed[236:244]),
        duration=float(fixed[244:252]),
        signals=tuple(
            Signal(label, float(p0), float(p1), int(d0), int(d1), int(n))
            for label, p0, p1, d0, d1, n in zip(
                labels, pmin, pmax, dmin, dmax, samples, strict=True
            )
        ),
    )
    if header.size != 256 * (count + 1):
        raise ValueError(f"header size is {header.size}, not {256 * (count + 1)}")
    if header.record_size == 0:
        raise ValueError("records have no samples")
    return header


def complete_records(header: Header, file_size: int) -> int:
    return max(file_size - header.size, 0) // header.record_size


def read_records(file: BinaryIO, header: Header, first: int, count: int) -> bytes:
    file.seek(header.size + first * header.record_size)
    return _read(file, count * header.record_size)


def split(data: bytes, header: Header) -> list[list[tuple[int, ...]]]:
    """Digital values of each signal: one tuple of samples for each record."""
    starts = accumulate((2 * s.samples for s in header.signals[:-1]), initial=0)
    return [
        [
            struct.unpack_from(f"<{signal.samples}h", data, record + start)
            for record in range(0, len(data), header.record_size)
        ]
        for signal, start in zip(header.signals, starts, strict=True)
    ]


def annotations(data: bytes, header: Header) -> list[Annotation]:
    """Annotations of each record, without the time-keeping annotations."""
    labels = [signal.label for signal in header.signals]
    index = labels.index("EDF Annotations")
    start = 2 * sum(signal.samples for signal in header.signals[:index])
    width = 2 * header.signals[index].samples
    return [
        annotation
        for offset in range(0, len(data), header.record_size)
        for annotation in _parse_tals(data[offset + start : offset + start + width])
    ]


def _parse_tals(raw: bytes) -> list[Annotation]:
    result = []
    for tal in raw.split(b"\0"):
        if not tal:
            continue
        timing, *texts = tal.split(b"\x14")
        onset, _, duration = timing.partition(b"\x15")
        result += [
            Annotation(float(onset), float(duration) if duration else None, text)
            for text in (raw_text.decode("utf-8") for raw_text in texts)
            if text
        ]
    return result


def _read(file: BinaryIO, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise ValueError(f"expected {size} bytes, got {len(data)}")
    return data
