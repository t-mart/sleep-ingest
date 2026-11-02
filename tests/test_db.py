from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg import sql

from sleep_ingest import cli, db
from tests import synthetic
from tests.conftest import (
    BRP_PATH,
    CDT,
    PLD_PATH,
    START,
    brp_record,
    pld_record,
    str_file,
)

FIRST_SAMPLE = datetime(2026, 9, 24, 4, 44, 33, tzinfo=UTC)


def count(conn: psycopg.Connection, table: str) -> int:
    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
    row = conn.execute(query).fetchone()
    assert row
    return row[0]


def rows_written(outcomes: list[cli.Outcome]) -> dict[str, int]:
    return {table: sum(o.rows.get(table, 0) for o in outcomes) for table in cli.TABLES}


def test_schema_can_be_created_again(conn):
    db.create_schema(conn)


def test_ingest_writes_each_table(conn, card):
    outcomes = cli.ingest(conn, card, CDT)

    assert [o.error for o in outcomes] == [None] * 5
    assert rows_written(outcomes) == {
        "waveform_raw": 3000,
        "therapy_raw": 30,
        "event": 2,
        "daily_summary": 1,
        "mask_session": 1,
    }
    assert conn.execute(
        "SELECT path, records FROM source_file ORDER BY path"
    ).fetchall() == [
        ("DATALOG/20260923/20260923_234433_BRP.edf", 2),
        ("DATALOG/20260923/20260923_234433_CSL.edf", 1),
        ("DATALOG/20260923/20260923_234433_EVE.edf", 2),
        ("DATALOG/20260923/20260923_234433_PLD.edf", 1),
    ]


def test_views_give_physical_values_and_null_outside_the_limits(conn, card):
    cli.ingest(conn, card, CDT)

    waveform = conn.execute(
        "SELECT flow, press FROM waveform WHERE time = %s",
        (FIRST_SAMPLE + timedelta(seconds=60, milliseconds=40),),
    ).fetchone()
    assert waveform == (pytest.approx(0.2), pytest.approx(10))
    assert count(conn, "waveform_raw") == 3000
    raw_flow = conn.execute(
        "SELECT flow FROM waveform_raw WHERE time = %s", (FIRST_SAMPLE,)
    ).fetchone()
    assert raw_flow == (-1,)

    therapy = conn.execute(
        "SELECT mask_press, resp_rate, min_vent, flow_lim FROM therapy WHERE time = %s",
        (FIRST_SAMPLE + timedelta(seconds=58),),
    ).fetchone()
    assert therapy == (pytest.approx(8), pytest.approx(15), pytest.approx(6), None)


def test_ingest_writes_events_and_therapy_days(conn, card):
    cli.ingest(conn, card, CDT)

    assert conn.execute(
        "SELECT time, kind, duration_s FROM event ORDER BY time"
    ).fetchall() == [
        (FIRST_SAMPLE + timedelta(seconds=12), "Obstructive Apnea", 11),
        (FIRST_SAMPLE + timedelta(seconds=70), "Arousal", 0),
    ]
    assert conn.execute(
        "SELECT day, duration_min, mask_events, ahi, settings->'S.EPR.Level'"
        " FROM daily_summary"
    ).fetchone() == (date(2026, 9, 23), 400, 2, pytest.approx(1.2), 2.0)
    noon = datetime(2026, 9, 23, 17, tzinfo=UTC)
    assert conn.execute(
        "SELECT slot, mask_on, mask_off FROM mask_session"
    ).fetchall() == [(0, noon + timedelta(minutes=704), noon + timedelta(minutes=1104))]


def test_second_ingest_writes_nothing(conn, card):
    cli.ingest(conn, card, CDT)

    outcomes = cli.ingest(conn, card, CDT)

    assert [(o.new, o.error) for o in outcomes] == [(False, None)] * 5
    assert set(rows_written(outcomes).values()) == {0}


def test_ingest_imports_only_the_new_records_of_a_growing_file(conn, card):
    synthetic.write(
        card, BRP_PATH, synthetic.edf(START, synthetic.BRP, [brp_record(1)])
    )
    cli.ingest(conn, card, CDT)
    records = [brp_record(1), brp_record(2), brp_record(3)]
    synthetic.write(card, BRP_PATH, synthetic.edf(START, synthetic.BRP, records))

    outcomes = cli.ingest(conn, card, CDT)

    assert rows_written(outcomes)["waveform_raw"] == 3000
    assert count(conn, "waveform_raw") == 4500
    assert conn.execute(
        "SELECT records FROM source_file WHERE path = %s", (BRP_PATH,)
    ).fetchone() == (3,)


def test_ingest_ignores_an_incomplete_last_record(conn, card):
    data = synthetic.edf(START, synthetic.BRP, [brp_record(1), brp_record(2)])
    synthetic.write(card, BRP_PATH, data[:-10])

    cli.ingest(conn, card, CDT)

    assert count(conn, "waveform_raw") == 1500


def test_file_with_fewer_records_fails_and_other_files_import(conn, card):
    cli.ingest(conn, card, CDT)
    conn.execute("UPDATE source_file SET records = 5 WHERE path = %s", (BRP_PATH,))
    other = "DATALOG/20260924/20260924_220000_PLD.edf"
    synthetic.write(
        card,
        other,
        synthetic.edf(datetime(2026, 9, 24, 22), synthetic.PLD, [pld_record(1)]),
    )

    outcomes = cli.ingest(conn, card, CDT)

    assert [(o.path, o.error) for o in outcomes if o.error] == [
        (BRP_PATH, "file has 2 records, 5 were imported")
    ]
    assert [o.path for o in outcomes if o.new] == [other]


def test_file_with_a_wrong_layout_fails(conn, card):
    synthetic.write(
        card, PLD_PATH, synthetic.edf(START, synthetic.BRP, [brp_record(1)])
    )

    outcomes = cli.ingest(conn, card, CDT)

    [failed] = [o for o in outcomes if o.error]
    assert failed.path == PLD_PATH
    assert "signals are" in (failed.error or "")
    assert count(conn, "therapy_raw") == 0


def test_rows_and_imported_records_commit_together(conn, card, monkeypatch):
    def fail(*_):
        raise psycopg.DataError("no space")

    monkeypatch.setattr(db, "set_imported_records", fail)
    outcomes = cli.ingest(conn, card, CDT)

    assert {o.error for o in outcomes if o.path != "STR.edf"} == {"no space"}
    assert count(conn, "waveform_raw") == 0
    assert count(conn, "event") == 0


def test_existing_rows_do_not_change(conn, card):
    cli.ingest(conn, card, CDT)
    conn.execute("UPDATE waveform_raw SET flow = 999 WHERE time = %s", (FIRST_SAMPLE,))
    conn.execute("DELETE FROM source_file")

    outcomes = cli.ingest(conn, card, CDT)

    assert rows_written(outcomes)["waveform_raw"] == 0
    assert conn.execute(
        "SELECT flow FROM waveform_raw WHERE time = %s", (FIRST_SAMPLE,)
    ).fetchone() == (999,)


def test_str_updates_days_that_changed(conn, card):
    cli.ingest(conn, card, CDT)
    synthetic.write(card, "STR.edf", str_file(duration=500))

    outcomes = cli.ingest(conn, card, CDT)

    assert rows_written(outcomes)["daily_summary"] == 1
    assert conn.execute("SELECT duration_min FROM daily_summary").fetchone() == (500,)


def test_file_without_records_is_valid(conn, card):
    synthetic.write(card, BRP_PATH, synthetic.edf(START, synthetic.BRP, []))

    outcomes = cli.ingest(conn, card, CDT)

    assert [o for o in outcomes if o.path == BRP_PATH] == [cli.Outcome(BRP_PATH)]
