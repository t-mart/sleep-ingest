import io
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sleep_ingest import airsense, edf
from tests import synthetic

CDT = timezone(timedelta(hours=-5))
START = datetime(2026, 9, 23, 23, 44, 33)


def decode(data: bytes) -> tuple[edf.Header, bytes]:
    file = io.BytesIO(data)
    header = edf.read_header(file)
    count = edf.complete_records(header, len(data))
    return header, edf.read_records(file, header, 0, count)


def pld_record(flow_lim: int) -> list[list[int]]:
    return [[i] * 30 for i in range(1, 9)] + [[flow_lim] * 30, [0]]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-05:00", CDT),
        ("+0530", timezone(timedelta(hours=5, minutes=30))),
        ("America/Chicago", ZoneInfo("America/Chicago")),
    ],
)
def test_parse_time_zone(value, expected):
    assert airsense.parse_time_zone(value) == expected


def test_parse_time_zone_rejects_unknown_names():
    with pytest.raises(KeyError):
        airsense.parse_time_zone("Mars/Olympus_Mons")


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        # Ambiguous: 01:30 happens twice. Use the offset from before the change.
        (datetime(2026, 11, 1, 1, 30), datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
        # Absent: 02:30 does not happen. Use the offset from before the change.
        (datetime(2026, 3, 8, 2, 30), datetime(2026, 3, 8, 8, 30, tzinfo=UTC)),
    ],
)
def test_to_utc_uses_the_offset_from_before_a_daylight_saving_change(local, expected):
    assert airsense.to_utc(local, ZoneInfo("America/Chicago")) == expected


@pytest.mark.parametrize("product", ["AirSense11AutoSet", "AirSense 11 AutoSet"])
def test_check_data_path_accepts_an_airsense_11(tmp_path, product):
    root = synthetic.write_card(tmp_path, product=product)
    synthetic.write(root, "STR.edf", b"")

    airsense.check_data_path(root)


def test_check_data_path_rejects_other_products(tmp_path):
    root = synthetic.write_card(tmp_path, product="AirSense10AutoSet")
    synthetic.write(root, "STR.edf", b"")

    with pytest.raises(ValueError, match="not an AirSense 11"):
        airsense.check_data_path(root)


def test_check_data_path_requires_str(tmp_path):
    with pytest.raises(ValueError, match="STR.edf"):
        airsense.check_data_path(synthetic.write_card(tmp_path))


def test_datalog_files_match_the_name_pattern(tmp_path):
    root = synthetic.write_card(tmp_path)
    paths = [
        "DATALOG/20260923/20260923_234433_BRP.edf",
        "DATALOG/20260923/20260923_234433_EVE.edf",
        "DATALOG/20260923/20260923_234433_SA2.edf",
        "DATALOG/20260923/notes.edf",
        "DATALOG/20260923_234433_PLD.edf",
    ]
    for path in paths:
        synthetic.write(root, path, b"")

    assert airsense.datalog_files(root) == paths[:2]


def test_check_layout_accepts_the_signal_map():
    for signals, kind in ((synthetic.BRP, "BRP"), (synthetic.PLD, "PLD")):
        header, _ = decode(synthetic.edf(START, signals, []))
        airsense.check_layout(header, airsense.LAYOUTS[kind])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda h: replace(h, signals=h.signals[::-1]), "signals are"),
        (lambda h: replace(h, duration=30.0), "records are"),
        (
            lambda h: replace(
                h, signals=(replace(h.signals[0], samples=750), *h.signals[1:])
            ),
            "samples per record",
        ),
        (
            lambda h: replace(
                h, signals=(replace(h.signals[0], physical_max=4), *h.signals[1:])
            ),
            "gain",
        ),
        (
            lambda h: replace(
                h,
                signals=(
                    replace(h.signals[0], physical_min=-1, physical_max=4),
                    *h.signals[1:],
                ),
            ),
            "offset",
        ),
    ],
)
def test_check_layout_rejects_a_different_header(change, message):
    header, _ = decode(synthetic.edf(START, synthetic.BRP, []))

    with pytest.raises(ValueError, match=message):
        airsense.check_layout(change(header), airsense.LAYOUTS["BRP"])


def test_series_gives_sample_times_and_digital_values():
    records = [[[0, 1] * 750, [2] * 1500, [0]], [[3] * 1500, [4] * 1500, [0]]]
    header, data = decode(synthetic.edf(START, synthetic.BRP, records))

    series = airsense.series(header, airsense.LAYOUTS["BRP"], data, 0, CDT)

    assert series.start == datetime(2026, 9, 24, 4, 44, 33, tzinfo=UTC)
    assert series.step == timedelta(milliseconds=40)
    assert series.columns["flow"][:3] == [0, 1, 0]
    assert series.columns["flow"][1500] == 3
    assert len(series.columns["press"]) == 3000
    assert series.limits == {"flow": (-1000, 1500), "press": (0, 2000)}


def test_series_starts_at_the_first_new_record():
    header, data = decode(synthetic.edf(START, synthetic.PLD, [pld_record(-1)]))

    series = airsense.series(header, airsense.LAYOUTS["PLD"], data, 2, CDT)

    assert series.start == datetime(2026, 9, 24, 4, 46, 33, tzinfo=UTC)
    assert series.step == timedelta(seconds=2)
    assert series.columns["flow_lim"] == [-1] * 30
    assert series.limits["flow_lim"] == (0, 100)


def test_events_skip_recording_starts_and_default_the_duration_to_zero():
    records = [
        synthetic.annotation_record(0, (0, None, "Recording starts")),
        synthetic.annotation_record(60, (61.5, 11, "Obstructive Apnea")),
        synthetic.annotation_record(120, (130, None, "Arousal")),
    ]
    header, data = decode(
        synthetic.edf(START, [synthetic.ANNOTATIONS, synthetic.CRC], records)
    )

    events = airsense.events(header, data, CDT)

    start = datetime(2026, 9, 24, 4, 44, 33, tzinfo=UTC)
    assert events == [
        airsense.Event(start + timedelta(seconds=61.5), "Obstructive Apnea", 11),
        airsense.Event(start + timedelta(seconds=130), "Arousal", 0),
    ]


def test_therapy_days_give_physical_values_settings_and_sessions():
    record = synthetic.str_record(
        date(2026, 10, 17),
        sessions=[(464, 591), (1091, 1574)],
        values={"Duration": 610, "AHI": 1, "Leak.95": 2, "SpO2.50": -1, "Mode": 1},
    )
    header, data = decode(
        synthetic.edf(
            datetime(2026, 10, 17, 12), synthetic.STR, [record], duration=86400
        )
    )

    [day] = airsense.therapy_days(header, data, CDT)

    assert day.day == date(2026, 10, 17)
    assert day.values["duration_min"] == 610
    assert day.values["ahi"] == pytest.approx(0.1)
    assert day.values["leak_95"] == pytest.approx(0.2)
    assert day.values["spo2_50"] is None
    assert day.settings == {
        "Mode": 1,
        "S.EPR.Level": pytest.approx(2),
        "HeatedTube": 0,
        "Humidifier": 0,
    }
    noon = datetime(2026, 10, 17, 17, tzinfo=UTC)
    assert day.sessions == (
        airsense.MaskSession(
            day.day, 0, noon + timedelta(minutes=464), noon + timedelta(minutes=591)
        ),
        # MaskOff is above its digital maximum, so it is NULL.
        airsense.MaskSession(day.day, 1, noon + timedelta(minutes=1091), None),
    )


def test_therapy_days_require_each_str_label():
    signals = [s for s in synthetic.STR if s.label != "AHI"]
    header, data = decode(synthetic.edf(START, signals, [], duration=86400))

    with pytest.raises(ValueError, match="has no AHI"):
        airsense.therapy_days(header, data, CDT)
