import json
import re
from pathlib import Path

import pytest

from sleep_ingest import cli
from tests.conftest import CDT

DASHBOARDS = sorted((Path(__file__).parent.parent / "dashboards").glob("*.json"))
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
