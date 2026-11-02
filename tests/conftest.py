import os
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from sleep_ingest import db
from tests import synthetic

CDT = timezone(timedelta(hours=-5))
START = datetime(2026, 9, 23, 23, 44, 33)
BRP_PATH = "DATALOG/20260923/20260923_234433_BRP.edf"
PLD_PATH = "DATALOG/20260923/20260923_234433_PLD.edf"
EVE_PATH = "DATALOG/20260923/20260923_234433_EVE.edf"
CSL_PATH = "DATALOG/20260923/20260923_234433_CSL.edf"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    """A connection with the schema, in a transaction that rolls back."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for database tests.")
    with (
        psycopg.connect(url, autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        db.create_schema(connection)
        yield connection


def brp_record(flow: int) -> list[list[int]]:
    return [[flow] * 1500, [500] * 1500, [0]]


def pld_record(flow_lim: int) -> list[list[int]]:
    values = [400, 400, 300, 10, 75, 25, 48, 0, flow_lim]
    return [[value] * 30 for value in values] + [[0]]


def str_file(duration: int = 400) -> bytes:
    record = synthetic.str_record(
        date(2026, 9, 23),
        sessions=[(704, 1104)],
        values={"Duration": duration, "MaskEvents": 2, "AHI": 12},
    )
    return synthetic.edf(
        datetime(2026, 9, 23, 12), synthetic.STR, [record], duration=86400
    )


@pytest.fixture
def card(tmp_path: Path) -> Path:
    """A data path with STR.edf and one session of each DATALOG type."""
    root = synthetic.write_card(tmp_path)
    annotations = [synthetic.ANNOTATIONS, synthetic.CRC]
    synthetic.write(root, "STR.edf", str_file())
    synthetic.write(
        root,
        BRP_PATH,
        synthetic.edf(START, synthetic.BRP, [brp_record(-1), brp_record(100)]),
    )
    synthetic.write(
        root, PLD_PATH, synthetic.edf(START, synthetic.PLD, [pld_record(-1)])
    )
    synthetic.write(
        root,
        EVE_PATH,
        synthetic.edf(
            START,
            annotations,
            [
                synthetic.annotation_record(
                    0, (0, None, "Recording starts"), (12, 11, "Obstructive Apnea")
                ),
                synthetic.annotation_record(60, (70, None, "Arousal")),
            ],
            duration=0,
        ),
    )
    synthetic.write(
        root,
        CSL_PATH,
        synthetic.edf(
            START,
            annotations,
            [synthetic.annotation_record(0, (0, None, "Recording starts"))],
            duration=0,
        ),
    )
    return root
