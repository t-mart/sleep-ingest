import json
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from sleep_ingest import cli
from tests.conftest import CDT

DASHBOARDS = sorted((Path(__file__).parent.parent / "dashboards").glob("*.json"))
TRENDS = Path(__file__).parent.parent / "dashboards" / "trends.json"
DATASOURCE = {
    "type": "grafana-postgresql-datasource",
    "uid": "sleep_ingest_timescaledb",
}
VARIABLES = {
    "$__timeFrom()": "'2026-09-20T00:00:00Z'",
    "$__timeTo()": "'2026-09-30T00:00:00Z'",
    "${TimeZone:sqlstring}": "'America/Chicago'",
}


def targets() -> list[tuple[str, dict]]:
    result = []
    for path in DASHBOARDS:
        board = json.loads(path.read_text())
        for panel in board["panels"]:
            result += [
                (f"{path.name}: {panel['title']}", target)
                for target in panel["targets"]
            ]
        result += [
            (f"{path.name}: {annotation['name']}", annotation["target"])
            for annotation in board["annotations"]["list"]
            if "target" in annotation
        ]
    return result


def expand(query: str) -> str:
    query = re.sub(
        r"\$__timeFilter\((\w+)\)", r"\1 BETWEEN $__timeFrom() AND $__timeTo()", query
    )
    query = re.sub(
        r"\$__timeGroupAlias\((\w+), \$__interval, NULL\)",
        r"time_bucket('1 minute', \1) AS time",
        query,
    )
    for name, value in VARIABLES.items():
        query = query.replace(name, value)
    assert "$" not in query
    return query


@pytest.mark.parametrize(("name", "target"), targets(), ids=[n for n, _ in targets()])
def test_query_runs(conn, card, name, target):
    cli.ingest(conn, card, CDT)

    assert target["datasource"] == DATASOURCE
    conn.execute(expand(target["rawSql"]).encode())


def trends_query(title: str) -> bytes:
    [panel] = [
        p for p in json.loads(TRENDS.read_text())["panels"] if p["title"] == title
    ]
    return expand(panel["targets"][0]["rawSql"]).encode()


def test_therapy_times_gives_the_minutes_in_each_period(conn, card):
    # The session in the card is from 23:44 to 06:24.
    cli.ingest(conn, card, CDT)

    cursor = conn.execute(trends_query("Therapy times"))
    [row] = cursor.fetchall()
    cells = dict(zip([column.name for column in cursor.description], row, strict=True))
    expected = {"12:00": 0, "23:30": 1, "23:45": 15, "06:15": 9, "06:30": 0}
    assert cells["time"] == datetime(2026, 9, 23, 17, tzinfo=UTC)
    assert {name: cells[name] for name in expected} == expected


@pytest.mark.parametrize(
    ("starts", "expected"),
    [
        # Minutes after noon: 23:00 every night.
        ([660, 660, 660], 100),
        # The second night starts at 01:00. For 8 of 48 hours, the state differs.
        ([660, 780, 660], 100 - 200 * 8 / 48),
    ],
)
def test_sleep_regularity_index(conn, starts, expected):
    for offset, start in enumerate(starts):
        day = date(2026, 9, 21) + timedelta(days=offset)
        mask_on = datetime.combine(day, time(12), CDT) + timedelta(minutes=start)
        conn.execute(
            "INSERT INTO daily_summary (day, duration_min, mask_events, settings)"
            " VALUES (%s, 480, 1, '{}')",
            [day],
        )
        conn.execute(
            "INSERT INTO mask_session VALUES (%s, 0, %s, %s)",
            [day, mask_on, mask_on + timedelta(hours=8)],
        )

    [(index,)] = conn.execute(trends_query("Sleep regularity index")).fetchall()
    assert float(index) == pytest.approx(expected)
