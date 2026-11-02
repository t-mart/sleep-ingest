"""Import the SD card files of a ResMed AirSense 11 into TimescaleDB."""

import argparse
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import tzinfo
from functools import partial
from pathlib import Path

import psycopg

from sleep_ingest import airsense, db, edf

TABLES = ("waveform_raw", "therapy_raw", "event", "daily_summary", "mask_session")
FILE_ERRORS = (OSError, ValueError, psycopg.DataError, psycopg.IntegrityError)


@dataclass(frozen=True)
class Outcome:
    path: str
    new: bool = False
    rows: Mapping[str, int] = field(default_factory=dict)
    error: str | None = None


def main() -> None:
    sys.exit(run(sys.argv[1:]))


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sleep-ingest", description=__doc__)
    parser.add_argument(
        "--data-path",
        default=os.environ.get("INPUT_DIR"),
        help="directory with the SD card layout (default: $INPUT_DIR)",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="libpq connection URI or keyword string (default: the PG* variables)",
    )
    parser.add_argument(
        "--time-zone",
        help="IANA zone name or UTC offset of the device clock"
        " (default: TimeZoneOffset in SETTINGS/CurrentSettings.json)",
    )
    args = parser.parse_args(argv)
    if not args.data_path:
        parser.error("give --data-path or set INPUT_DIR")

    root = Path(args.data_path)
    try:
        airsense.check_data_path(root)
        zone = airsense.parse_time_zone(
            args.time_zone or airsense.device_time_zone(root)
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"{root}: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(args.database_url, autocommit=True) as conn:
            with conn.transaction():
                db.create_schema(conn)
            outcomes = ingest(conn, root, zone)
    except psycopg.Error as error:
        print(f"database: {error}", file=sys.stderr)
        return 2

    report(outcomes)
    return 1 if any(outcome.error for outcome in outcomes) else 0


def ingest(conn: psycopg.Connection, root: Path, zone: tzinfo) -> list[Outcome]:
    imported = db.imported_records(conn)
    return [
        _attempt("STR.edf", partial(_import_str, conn, root, zone)),
        *(
            _attempt(path, partial(_import_datalog, conn, root, path, zone, imported))
            for path in airsense.datalog_files(root)
        ),
    ]


def report(outcomes: list[Outcome]) -> None:
    for outcome in outcomes:
        if outcome.error:
            print(f"{outcome.path}: {outcome.error}", file=sys.stderr)
    rows = sum((Counter(outcome.rows) for outcome in outcomes), Counter())
    succeeded = [outcome for outcome in outcomes if not outcome.error]
    print(f"Files with new records: {sum(o.new for o in succeeded)}")
    print(f"Files without new records: {sum(not o.new for o in succeeded)}")
    print(f"Failed files: {len(outcomes) - len(succeeded)}")
    print("Rows written:")
    for table in TABLES:
        print(f"  {table}: {rows[table]}")


def _attempt(path: str, action: Callable[[], Outcome]) -> Outcome:
    try:
        return action()
    except FILE_ERRORS as error:
        return Outcome(path, error=str(error) or repr(error))


def _import_str(conn: psycopg.Connection, root: Path, zone: tzinfo) -> Outcome:
    with (root / "STR.edf").open("rb") as file:
        header = edf.read_header(file)
        count = edf.complete_records(header, os.fstat(file.fileno()).st_size)
        data = edf.read_records(file, header, 0, count)
    days = airsense.therapy_days(header, data, zone)
    with conn.transaction():
        rows = db.write_therapy_days(conn, days)
    return Outcome("STR.edf", new=any(rows.values()), rows=rows)


def _import_datalog(
    conn: psycopg.Connection,
    root: Path,
    path: str,
    zone: tzinfo,
    imported: Mapping[str, int],
) -> Outcome:
    kind = path[-7:-4]
    layout = airsense.LAYOUTS.get(kind)
    done = imported.get(path, 0)
    with (root / path).open("rb") as file:
        header = edf.read_header(file)
        if layout:
            airsense.check_layout(header, layout)
        count = edf.complete_records(header, os.fstat(file.fileno()).st_size)
        if count != header.records:
            print(
                f"{path}: header gives {header.records} records, file has {count}",
                file=sys.stderr,
            )
        if count < done:
            raise ValueError(f"file has {count} records, {done} were imported")
        if count == done:
            return Outcome(path)
        data = edf.read_records(file, header, done, count - done)

    with conn.transaction():
        if layout:
            series = airsense.series(header, layout, data, done, zone)
            rows = {layout.table: db.write_series(conn, layout.table, series)}
        else:
            rows = {"event": db.write_events(conn, airsense.events(header, data, zone))}
        db.set_imported_records(conn, path, count)
    return Outcome(path, new=True, rows=rows)
