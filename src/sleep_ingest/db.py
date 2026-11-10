"""Schema and writes. The caller owns the transactions."""

from datetime import timezone, tzinfo
from importlib.resources import files

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from sleep_ingest.airsense import Event, Series, TherapyDay

SCHEMA = files(__package__).joinpath("schema.sql").read_bytes()


def create_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)


def reset_import(conn: psycopg.Connection) -> None:
    conn.execute(
        "TRUNCATE waveform_raw, therapy_raw, event, mask_session, daily_summary,"
        " source_file, import_clock"
    )


def check_import_offset(
    conn: psycopg.Connection, zone: tzinfo, device_zone: timezone
) -> None:
    seconds = int(device_zone.utcoffset(None).total_seconds())
    conn.execute("LOCK TABLE import_clock IN EXCLUSIVE MODE")
    row = conn.execute(
        "SELECT offset_seconds, source_time_zone FROM import_clock"
    ).fetchone()
    if row:
        if row != (seconds, str(zone)):
            raise ValueError(
                "device offset changed or export zone changed; use a separate database or --rebuild"
            )
        return
    for table in (
        "source_file",
        "daily_summary",
        "mask_session",
        "waveform_raw",
        "therapy_raw",
        "event",
    ):
        [exists] = conn.execute(
            sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(table))
        ).fetchone() or (False,)
        if exists:
            raise ValueError(
                "stored timestamps have no offset record; use --rebuild with a complete archive"
            )
    conn.execute(
        "INSERT INTO import_clock (offset_seconds, source_time_zone) VALUES (%s, %s)",
        (seconds, str(zone)),
    )


def imported_records(conn: psycopg.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT path, records FROM source_file").fetchall())


def set_imported_records(conn: psycopg.Connection, path: str, records: int) -> None:
    conn.execute(
        "INSERT INTO source_file (path, records) VALUES (%s, %s)"
        " ON CONFLICT (path) DO UPDATE SET records = EXCLUDED.records",
        (path, records),
    )


def write_series(conn: psycopg.Connection, table: str, series: Series) -> int:
    """Insert new rows. Digital values outside the limits become NULL."""
    names = list(series.columns)
    query = sql.SQL(
        "INSERT INTO {table} (time, {names})"
        " SELECT %(start)s + (n - 1) * %(step)s, {in_limits}"
        " FROM unnest({arrays}) WITH ORDINALITY AS s({names}, n)"
        " ON CONFLICT DO NOTHING"
    ).format(
        table=sql.Identifier(table),
        names=sql.SQL(", ").join(map(sql.Identifier, names)),
        in_limits=sql.SQL(", ").join(
            sql.SQL("CASE WHEN {0} BETWEEN {1} AND {2} THEN {0} END").format(
                sql.Identifier(name), *map(sql.Literal, series.limits[name])
            )
            for name in names
        ),
        arrays=sql.SQL(", ").join(
            sql.SQL("{}::smallint[]").format(sql.Placeholder(name)) for name in names
        ),
    )
    arrays = {
        name: "{" + ",".join(map(str, values)) + "}"
        for name, values in series.columns.items()
    }
    return conn.execute(
        query, {"start": series.start, "step": series.step, **arrays}
    ).rowcount


def write_events(conn: psycopg.Connection, events: list[Event]) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO event (time, kind, duration_s) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [(e.time, e.kind, e.duration_s) for e in events],
        )
        return cur.rowcount


def write_therapy_days(
    conn: psycopg.Connection, days: list[TherapyDay]
) -> dict[str, int]:
    """Insert or update days and sessions. Unchanged rows are not written."""
    if not days:
        return {"daily_summary": 0, "mask_session": 0}
    names = ["day", "day_start", *days[0].values, "settings"]
    daily = sql.SQL(
        "INSERT INTO daily_summary ({}) VALUES ({}) ON CONFLICT (day) DO UPDATE"
        " SET {} WHERE daily_summary IS DISTINCT FROM EXCLUDED"
    ).format(
        sql.SQL(", ").join(map(sql.Identifier, names)),
        sql.SQL(", ").join(map(sql.Placeholder, names)),
        sql.SQL(", ").join(
            sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(name))
            for name in names[1:]
        ),
    )
    with conn.cursor() as cur:
        cur.executemany(
            daily,
            [
                {
                    "day": d.day,
                    "day_start": d.start,
                    **d.values,
                    "settings": Jsonb(d.settings),
                }
                for d in days
            ],
        )
        daily_rows = cur.rowcount
        cur.executemany(
            "DELETE FROM mask_session WHERE day = %s AND NOT (slot = ANY(%s::smallint[]))",
            [(d.day, [s.slot for s in d.sessions]) for d in days],
        )
        removed_sessions = cur.rowcount
        cur.executemany(
            "INSERT INTO mask_session (day, slot, mask_on, mask_off)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (day, slot) DO UPDATE"
            " SET mask_on = EXCLUDED.mask_on, mask_off = EXCLUDED.mask_off"
            " WHERE mask_session IS DISTINCT FROM EXCLUDED",
            [(s.day, s.slot, s.mask_on, s.mask_off) for d in days for s in d.sessions],
        )
        return {
            "daily_summary": daily_rows,
            "mask_session": removed_sessions + cur.rowcount,
        }
