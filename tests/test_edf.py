import io
from datetime import datetime

import pytest

from sleep_ingest import edf
from tests.synthetic import ANNOTATIONS, BRP, CRC, annotation_record
from tests.synthetic import edf as edf_file

START = datetime(2026, 9, 23, 23, 44, 33)


def brp_record(value: int) -> list[list[int]]:
    return [[value] * 1500, [-value] * 1500, [7]]


def test_header_gives_start_signals_and_record_layout():
    header = edf.read_header(io.BytesIO(edf_file(START, BRP, [brp_record(1)])))

    assert header.start == START
    assert header.size == 256 * 4
    assert header.records == 1
    assert header.duration == 60
    assert [s.label for s in header.signals] == ["Flow.40ms", "Press.40ms", "Crc16"]
    assert header.signals[0].gain == pytest.approx(0.002)
    assert header.signals[0].offset == pytest.approx(0)
    assert header.record_size == 2 * 3001


def test_complete_records_ignores_an_incomplete_last_record():
    data = edf_file(START, BRP, [brp_record(1), brp_record(2)], header_records=-1)
    header = edf.read_header(io.BytesIO(data))

    assert edf.complete_records(header, len(data) - 1) == 1
    assert edf.complete_records(header, len(data)) == 2
    assert edf.complete_records(header, header.size) == 0


def test_split_gives_the_samples_of_each_signal_and_record():
    data = edf_file(START, BRP, [brp_record(1), brp_record(-3)])
    file = io.BytesIO(data)
    header = edf.read_header(file)

    flow, press, crc = edf.split(edf.read_records(file, header, 0, 2), header)

    assert flow == [(1,) * 1500, (-3,) * 1500]
    assert press == [(-1,) * 1500, (3,) * 1500]
    assert crc == [(7,), (7,)]


def test_read_records_starts_at_a_record_index():
    file = io.BytesIO(edf_file(START, BRP, [brp_record(1), brp_record(2)]))
    header = edf.read_header(file)

    flow, _, _ = edf.split(edf.read_records(file, header, 1, 1), header)

    assert flow == [(2,) * 1500]


def test_read_records_fails_past_the_end_of_the_file():
    file = io.BytesIO(edf_file(START, BRP, [brp_record(1)]))
    header = edf.read_header(file)

    with pytest.raises(ValueError, match="expected"):
        edf.read_records(file, header, 0, 2)


def test_header_with_a_wrong_size_fails():
    data = bytearray(edf_file(START, BRP, [brp_record(1)]))
    data[184:192] = b"512     "

    with pytest.raises(ValueError, match="header size"):
        edf.read_header(io.BytesIO(bytes(data)))


def test_annotations_of_each_record_without_crc_bytes():
    records = [
        annotation_record(0, (12.5, 10, "Obstructive Apnea")),
        annotation_record(60, (61, None, "Arousal"), (70, 0, "Hypopnea")),
    ]
    data = edf_file(START, [ANNOTATIONS, CRC], records, duration=0)
    file = io.BytesIO(data)
    header = edf.read_header(file)

    annotations = edf.annotations(edf.read_records(file, header, 0, 2), header)

    assert annotations == [
        edf.Annotation(12.5, 10, "Obstructive Apnea"),
        edf.Annotation(61, None, "Arousal"),
        edf.Annotation(70, 0, "Hypopnea"),
    ]
