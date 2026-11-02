"""ResMed AirSense 11 SD card layout, signal map, and transforms."""

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from itertools import chain
from pathlib import Path
from zoneinfo import ZoneInfo

from sleep_ingest import edf

DATALOG_FILE = re.compile(r"DATALOG/\d{8}/\d{8}_\d{6}_(BRP|PLD|EVE|CSL)\.edf")
UTC_OFFSET = re.compile(r"([+-])(\d{2}):?(\d{2})")
RECORD_SECONDS = 60


@dataclass(frozen=True)
class Channel:
    label: str
    column: str | None  # None: not stored
    samples: int  # per record
    gain: float


@dataclass(frozen=True)
class Layout:
    table: str
    channels: tuple[Channel, ...]


LAYOUTS = {
    "BRP": Layout(
        "waveform_raw",
        (
            Channel("Flow.40ms", "flow", 1500, 0.002),
            Channel("Press.40ms", "press", 1500, 0.02),
            Channel("Crc16", None, 1, 1),
        ),
    ),
    "PLD": Layout(
        "therapy_raw",
        (
            Channel("MaskPress.2s", "mask_press", 30, 0.02),
            Channel("Press.2s", "press", 30, 0.02),
            Channel("EprPress.2s", "epr_press", 30, 0.02),
            Channel("Leak.2s", "leak", 30, 0.02),
            Channel("RespRate.2s", "resp_rate", 30, 0.2),
            Channel("TidVol.2s", "tid_vol", 30, 0.02),
            Channel("MinVent.2s", "min_vent", 30, 0.125),
            Channel("Snore.2s", "snore", 30, 0.02),
            Channel("FlowLim.2s", "flow_lim", 30, 0.01),
            Channel("Crc16", None, 1, 1),
        ),
    ),
}

# STR.edf labels with one value for each therapy day, and their
# daily_summary columns.
DAILY_COLUMNS = {
    "Duration": "duration_min",
    "MaskEvents": "mask_events",
    "AHI": "ahi",
    "HI": "hi",
    "AI": "ai",
    "OAI": "oai",
    "CAI": "cai",
    "UAI": "uai",
    "RIN": "rin",
    "CSR": "csr_min",
    "MaskPress.50": "mask_press_50",
    "MaskPress.95": "mask_press_95",
    "MaskPress.Max": "mask_press_max",
    "TgtIPAP.50": "tgt_ipap_50",
    "TgtIPAP.95": "tgt_ipap_95",
    "TgtIPAP.Max": "tgt_ipap_max",
    "TgtEPAP.50": "tgt_epap_50",
    "TgtEPAP.95": "tgt_epap_95",
    "TgtEPAP.Max": "tgt_epap_max",
    "Leak.50": "leak_50",
    "Leak.70": "leak_70",
    "Leak.95": "leak_95",
    "Leak.Max": "leak_max",
    "RespRate.50": "resp_rate_50",
    "RespRate.95": "resp_rate_95",
    "RespRate.Max": "resp_rate_max",
    "TidVol.50": "tid_vol_50",
    "TidVol.95": "tid_vol_95",
    "TidVol.Max": "tid_vol_max",
    "MinVent.50": "min_vent_50",
    "MinVent.95": "min_vent_95",
    "MinVent.Max": "min_vent_max",
    "SpO2.50": "spo2_50",
    "SpO2.95": "spo2_95",
    "SpO2.Max": "spo2_max",
    "SpO2Thresh": "spo2_thresh_min",
    "BlowPress.5": "blow_press_5",
    "BlowPress.95": "blow_press_95",
    "Flow.5": "flow_5",
    "Flow.95": "flow_95",
    "BlowFlow.50": "blow_flow_50",
    "AmbHumidity.50": "amb_humidity_50",
    "HumTemp.50": "hum_temp_50",
    "HTubeTemp.50": "htube_temp_50",
    "HTubePow.50": "htube_pow_50",
    "HumPow.50": "hum_pow_50",
}
SETTING_LABELS = ("Mode", "HeatedTube", "Humidifier")
STR_LABELS = {"Date", "MaskOn", "MaskOff", *DAILY_COLUMNS, *SETTING_LABELS}


@dataclass(frozen=True)
class Series:
    start: datetime  # time of the first sample, UTC
    step: timedelta  # between samples
    columns: dict[str, list[int]]  # digital values
    limits: dict[str, tuple[int, int]]  # digital limits


@dataclass(frozen=True)
class Event:
    time: datetime
    kind: str
    duration_s: float


@dataclass(frozen=True)
class MaskSession:
    day: date
    slot: int
    mask_on: datetime
    mask_off: datetime | None


@dataclass(frozen=True)
class TherapyDay:
    day: date
    values: dict[str, float | None]  # daily_summary columns
    settings: dict[str, float | None]
    sessions: tuple[MaskSession, ...]


def check_data_path(root: Path) -> None:
    identification = json.loads((root / "Identification.json").read_text())
    product = identification["FlowGenerator"]["IdentificationProfiles"]["Product"]
    # The device writes "AirSense11AutoSet" or "AirSense 11 AutoSet".
    if not product["ProductName"].replace(" ", "").startswith("AirSense11"):
        raise ValueError(f"{product['ProductName']} is not an AirSense 11")
    if not (root / "STR.edf").is_file():
        raise ValueError("STR.edf is missing")
    if not (root / "DATALOG").is_dir():
        raise ValueError("DATALOG is missing")


def device_time_zone(root: Path) -> str:
    settings = json.loads((root / "SETTINGS" / "CurrentSettings.json").read_text())
    features = settings["FlowGenerator"]["SettingProfiles"]["FeatureProfiles"]
    return features["TimeZoneFeature"]["TimeZoneOffset"]


def parse_time_zone(value: str) -> tzinfo:
    """An IANA zone name, or a UTC offset such as -05:00."""
    match = UTC_OFFSET.fullmatch(value)
    if not match:
        return ZoneInfo(value)
    sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    return timezone(-offset if sign == "-" else offset)


def to_utc(local: datetime, zone: tzinfo) -> datetime:
    # fold=0 takes the UTC offset from before a daylight saving time change.
    return local.replace(tzinfo=zone, fold=0).astimezone(UTC)


def datalog_files(root: Path) -> list[str]:
    paths = (p.relative_to(root).as_posix() for p in root.glob("DATALOG/*/*.edf"))
    return sorted(path for path in paths if DATALOG_FILE.fullmatch(path))


def check_layout(header: edf.Header, layout: Layout) -> None:
    labels = [signal.label for signal in header.signals]
    expected = [channel.label for channel in layout.channels]
    if labels != expected:
        raise ValueError(f"signals are {labels}, not {expected}")
    if header.duration != RECORD_SECONDS:
        raise ValueError(f"records are {header.duration} s, not {RECORD_SECONDS} s")
    for signal, channel in zip(header.signals, layout.channels, strict=True):
        if signal.samples != channel.samples:
            raise ValueError(
                f"{signal.label} has {signal.samples} samples per record,"
                f" not {channel.samples}"
            )
        if not math.isclose(signal.gain, channel.gain, rel_tol=1e-9):
            raise ValueError(
                f"{signal.label} gain is {signal.gain}, not {channel.gain}"
            )
        if not math.isclose(signal.offset, 0, abs_tol=1e-9):
            raise ValueError(f"{signal.label} offset is {signal.offset}, not 0")


def series(
    header: edf.Header, layout: Layout, data: bytes, first: int, zone: tzinfo
) -> Series:
    """Samples of the records from index `first` on, as `data` holds them."""
    stored = [
        (column, signal, values)
        for channel, signal, values in zip(
            layout.channels, header.signals, edf.split(data, header), strict=True
        )
        if (column := channel.column)
    ]
    return Series(
        start=to_utc(header.start, zone) + timedelta(seconds=first * RECORD_SECONDS),
        step=timedelta(seconds=RECORD_SECONDS) / stored[0][1].samples,
        columns={column: list(chain(*values)) for column, _, values in stored},
        limits={
            column: (signal.digital_min, signal.digital_max)
            for column, signal, _ in stored
        },
    )


def events(header: edf.Header, data: bytes, zone: tzinfo) -> list[Event]:
    start = to_utc(header.start, zone)
    return [
        Event(start + timedelta(seconds=a.onset), a.text, a.duration or 0.0)
        for a in edf.annotations(data, header)
        if a.text != "Recording starts"
    ]


def therapy_days(header: edf.Header, data: bytes, zone: tzinfo) -> list[TherapyDay]:
    labels = [signal.label for signal in header.signals]
    missing = STR_LABELS.difference(labels)
    if missing:
        raise ValueError(f"STR.edf has no {', '.join(sorted(missing))}")
    values = {
        signal.label: _physical(signal, digital)
        for signal, digital in zip(header.signals, edf.split(data, header), strict=True)
    }
    settings = [
        label for label in labels if label.startswith("S.") or label in SETTING_LABELS
    ]
    return [
        _therapy_day(
            {label: column[record] for label, column in values.items()}, settings, zone
        )
        for record in range(len(values["Date"]))
    ]


def _therapy_day(
    record: dict[str, list[float | None]], settings: list[str], zone: tzinfo
) -> TherapyDay:
    day = date(1970, 1, 1) + timedelta(days=_required(record["Date"][0], "Date"))
    noon = to_utc(datetime.combine(day, time(12)), zone)
    return TherapyDay(
        day=day,
        values={column: record[label][0] for label, column in DAILY_COLUMNS.items()},
        settings={label: record[label][0] for label in settings},
        sessions=tuple(
            MaskSession(
                day,
                slot,
                noon + timedelta(minutes=on),
                None if off is None else noon + timedelta(minutes=off),
            )
            for slot, (on, off) in enumerate(
                zip(record["MaskOn"], record["MaskOff"], strict=True)
            )
            if on is not None
        ),
    )


def _physical(
    signal: edf.Signal, digital: list[tuple[int, ...]]
) -> list[list[float | None]]:
    """Physical values, with None for digital values outside the limits."""
    return [
        [
            signal.offset + d * signal.gain
            if signal.digital_min <= d <= signal.digital_max
            else None
            for d in row
        ]
        for row in digital
    ]


def _required(value: float | None, label: str) -> int:
    if value is None:
        raise ValueError(f"{label} is out of range")
    return round(value)
