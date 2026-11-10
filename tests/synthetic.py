"""Small synthetic AirSense 11 SD card files."""

import json
import struct
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path

from sleep_ingest.airsense import DAILY_COLUMNS
from sleep_ingest.edf import Signal

Record = Sequence[Sequence[int]]  # digital values of each signal

CRC = Signal("Crc16", -32768, 32767, -32768, 32767, 1)
ANNOTATIONS = Signal("EDF Annotations", -32768, 32767, -32768, 32767, 60)
BRP = (
    Signal("Flow.40ms", -2, 3, -1000, 1500, 1500),
    Signal("Press.40ms", 0, 40, 0, 2000, 1500),
    CRC,
)
PLD = (
    Signal("MaskPress.2s", 0, 40, 0, 2000, 30),
    Signal("Press.2s", 0, 50, 0, 2500, 30),
    Signal("EprPress.2s", 0, 30, 0, 1500, 30),
    Signal("Leak.2s", 0, 2, 0, 100, 30),
    Signal("RespRate.2s", 0, 90, 0, 450, 30),
    Signal("TidVol.2s", 0, 4, 0, 200, 30),
    Signal("MinVent.2s", 0, 30, 0, 240, 30),
    Signal("Snore.2s", 0, 5, 0, 250, 30),
    Signal("FlowLim.2s", 0, 1, 0, 100, 30),
    CRC,
)
STR = (
    Signal("Date", 0, 24836, 0, 24836, 1),
    Signal("MaskOn", 0, 1440, 0, 1440, 20),
    Signal("MaskOff", 0, 1440, 0, 1440, 20),
    *(
        Signal(label, 0, 1440, 0, 1440, 1)
        if label in ("Duration", "MaskEvents")
        else Signal(label, 0, 100, 0, 1000, 1)
        for label in DAILY_COLUMNS
    ),
    Signal("Mode", 0, 16, 0, 16, 1),
    Signal("S.EPR.Level", 1, 3, 50, 150, 1),
    Signal("HeatedTube", 0, 16, 0, 16, 1),
    Signal("Humidifier", 0, 16, 0, 16, 1),
    CRC,
)


def edf(
    start: datetime,
    signals: Sequence[Signal],
    records: Sequence[Record],
    *,
    duration: float = 60,
    header_records: int | None = None,
) -> bytes:
    count = len(signals)

    def field(value: object, width: int) -> bytes:
        return str(value).ljust(width)[:width].encode("ascii")

    columns = [
        [field(s.label, 16) for s in signals],
        [field("", 80) for _ in signals],
        [field("", 8) for _ in signals],
        [field(s.physical_min, 8) for s in signals],
        [field(s.physical_max, 8) for s in signals],
        [field(s.digital_min, 8) for s in signals],
        [field(s.digital_max, 8) for s in signals],
        [field("", 80) for _ in signals],
        [field(s.samples, 8) for s in signals],
        [field("", 32) for _ in signals],
    ]
    header = b"".join(
        [
            field(0, 8),
            field("X X X X", 80),
            field("Startdate X X X X", 80),
            start.strftime("%d.%m.%y%H.%M.%S").encode("ascii"),
            field(256 * (count + 1), 8),
            field("", 44),
            field(len(records) if header_records is None else header_records, 8),
            field(duration, 8),
            field(count, 4),
            *(value for column in columns for value in column),
        ]
    )
    body = b"".join(
        struct.pack(f"<{len(values)}h", *values)
        for record in records
        for values in record
    )
    return header + body


def annotation_record(
    onset: float, *annotations: tuple[float, float | None, str]
) -> Record:
    """A time-keeping annotation, then the annotations, then a Crc16 sample."""
    raw = f"+{onset:g}\x14\x14\0"
    for start, duration, text in annotations:
        timing = f"+{start:g}" if duration is None else f"+{start:g}\x15{duration:g}"
        raw += f"{timing}\x14{text}\x14\0"
    padded = raw.encode("utf-8").ljust(2 * ANNOTATIONS.samples, b"\0")
    # The Crc16 bytes spell "AA"; they must not appear as text.
    return [struct.unpack(f"<{ANNOTATIONS.samples}h", padded), (0x4141,)]


def str_record(
    day: date,
    sessions: Sequence[tuple[int, int]] = (),
    values: Mapping[str, int] | None = None,
) -> Record:
    """Digital values. Unused MaskOn and MaskOff samples are -1."""
    unused = [-1] * (20 - len(sessions))
    given = {
        "Date": [(day - date(1970, 1, 1)).days],
        "MaskOn": [on for on, _ in sessions] + unused,
        "MaskOff": [off for _, off in sessions] + unused,
        "S.EPR.Level": [100],
        **{label: [value] for label, value in (values or {}).items()},
    }
    return [given.get(s.label, [0] * s.samples) for s in STR]


def write_card(root: Path, product: str = "AirSense11AutoSet") -> Path:
    """Identification, device offset, and an empty DATALOG."""
    _write_json(
        root / "Identification.json",
        {
            "FlowGenerator": {
                "IdentificationProfiles": {"Product": {"ProductName": product}}
            }
        },
    )
    _write_json(
        root / "SETTINGS" / "CurrentSettings.json",
        {
            "FlowGenerator": {
                "SettingProfiles": {
                    "FeatureProfiles": {"TimeZoneFeature": {"TimeZoneOffset": "-05:00"}}
                }
            }
        },
    )
    (root / "DATALOG").mkdir()
    return root


def write(root: Path, path: str, data: bytes) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
