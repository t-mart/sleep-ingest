"""Import the SD card files of a ResMed AirSense 11 into TimescaleDB."""

import argparse
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timezone, tzinfo
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

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
        help="fixed device UTC offset (default: TimeZoneOffset in CurrentSettings.json)",
    )
    parser.add_argument(
        "--export-time-zone",
        default=os.environ.get("EXPORT_TIME_ZONE"),
        help="SomnoTrace export IANA zone (default: $EXPORT_TIME_ZONE; otherwise device time)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="replace all imported data from a complete card archive; roll back on error",
    )
    args = parser.parse_args(argv)
    if not args.data_path:
        parser.error("give --data-path or set INPUT_DIR")

    root = Path(args.data_path)
    try:
        airsense.check_data_path(root)
        device_zone = (
            airsense.parse_time_zone(args.time_zone)
            if args.time_zone
            else airsense.device_time_zone(root)
        )
        zone = ZoneInfo(args.export_time_zone) if args.export_time_zone else device_zone
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"{root}: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    try:
        with psycopg.connect(args.database_url, autocommit=True) as conn:
            with conn.transaction():
                db.create_schema(conn)
            outcomes = ingest(
                conn, root, zone, device_zone=device_zone, rebuild=args.rebuild
            )
    except (psycopg.Error, OSError, ValueError) as error:
        print(f"database: {error}", file=sys.stderr)
        return 2

    report(outcomes)
    return 1 if any(outcome.error for outcome in outcomes) else 0


def ingest(
    conn: psycopg.Connection,
    root: Path,
    zone: tzinfo,
    *,
    device_zone: timezone | None = None,
    rebuild: bool = False,
) -> list[Outcome]:
    if device_zone is None:
        device_zone = (
            zone if isinstance(zone, timezone) else airsense.device_time_zone(root)
        )
    if rebuild:
        with conn.transaction():
            _check_archive(conn, root, zone)
            db.reset_import(conn)
            outcomes = ingest(conn, root, zone, device_zone=device_zone)
            if any(outcome.error for outcome in outcomes):
                raise ValueError("rebuild failed; all previous data remains unchanged")
            return outcomes
    with conn.transaction():
        db.check_import_offset(conn, zone, device_zone)
    imported = db.imported_records(conn)
    actions = [
        ("STR.edf", partial(_import_str, conn, root, zone, device_zone)),
        *(
            (path, partial(_import_datalog, conn, root, path, zone, imported))
            for path in airsense.datalog_files(root)
        ),
    ]
    outcomes = []
    for number, (path, action) in enumerate(actions, start=1):
        outcome = _attempt(path, action)
        if outcome.new or outcome.error:
            print(f"[{number}/{len(actions)}] {describe(outcome)}", file=sys.stderr)
        outcomes.append(outcome)
    return outcomes


def _check_archive(conn: psycopg.Connection, root: Path, zone: tzinfo) -> None:
    for path, records in db.imported_records(conn).items():
        with (root / path).open("rb") as file:
            header = edf.read_header(file)
            count = edf.complete_records(header, os.fstat(file.fileno()).st_size)
        if count < records:
            raise ValueError(
                f"incomplete archive: {path} has {count} of {records} records"
            )
    days = {day.day for day in _read_str(root, zone)}
    stored = {day for (day,) in conn.execute("SELECT day FROM daily_summary")}
    if missing := stored - days:
        raise ValueError(
            f"incomplete archive: STR.edf lacks {len(missing)} stored days"
        )


def describe(outcome: Outcome) -> str:
    if outcome.error:
        return f"{outcome.path}: {outcome.error}"
    rows = ", ".join(f"{n} rows in {table}" for table, n in outcome.rows.items())
    return f"{outcome.path}: {rows}"


def report(outcomes: list[Outcome]) -> None:
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


def _import_str(
    conn: psycopg.Connection, root: Path, zone: tzinfo, device_zone: timezone
) -> Outcome:
    days = _read_str(root, zone, device_zone)
    for day in days:
        if day.values["duration_min"] and not day.sessions:
            print(
                f"STR.edf {day.day}: mask times unavailable or inconsistent; summary retained",
                file=sys.stderr,
            )
    with conn.transaction():
        rows = db.write_therapy_days(conn, days)
    return Outcome("STR.edf", new=any(rows.values()), rows=rows)


def _read_str(
    root: Path, zone: tzinfo, device_zone: timezone | None = None
) -> list[airsense.TherapyDay]:
    with (root / "STR.edf").open("rb") as file:
        header = edf.read_header(file)
        count = edf.complete_records(header, os.fstat(file.fileno()).st_size)
        data = edf.read_records(file, header, 0, count)
    return airsense.therapy_days(header, data, zone, device_zone=device_zone)


def _import_datalog(
    conn: psycopg.Connection,
    root: Path,
    path: str,
    zone: tzinfo,
    imported: Mapping[str, int],
) -> Outcome:
    kind = path[-7:-4]
    layout = airsense.LAYOUTS.get(kind)
    done = imported.get(path)
    start = done or 0
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
        if count < start:
            raise ValueError(f"file has {count} records, {start} were imported")
        if count == done:
            return Outcome(path)
        data = edf.read_records(file, header, start, count - start)

    with conn.transaction():
        if layout:
            series = airsense.series(header, layout, data, start, zone)
            rows = {layout.table: db.write_series(conn, layout.table, series)}
        else:
            rows = {"event": db.write_events(conn, airsense.events(header, data, zone))}
        db.set_imported_records(conn, path, count)
    return Outcome(path, new=count > start, rows=rows)
