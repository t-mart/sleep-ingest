# sleep-ingest

`sleep-ingest` imports the SD card files of a ResMed AirSense 11 CPAP device
into PostgreSQL with the TimescaleDB extension. Grafana dashboards in
[`dashboards/`](dashboards/) show the data.

## Data

| Source | Table | Contents |
|---|---|---|
| `STR.edf` | `daily_summary`, `mask_session` | One row for each therapy day: usage, AHI, percentiles, settings. Mask on and mask off times. |
| `DATALOG/*/*_BRP.edf` | `waveform_raw` | Flow and pressure at 25 Hz. |
| `DATALOG/*/*_PLD.edf` | `therapy_raw` | Pressure, leak, breathing rate, tidal volume, minute ventilation, snore, and flow limitation every 2 seconds. |
| `DATALOG/*/*_EVE.edf`, `*_CSL.edf` | `event` | Apneas, hypopneas, arousals, and Cheyne-Stokes respiration. |

The `_raw` tables store the digital values from the files.
The `waveform` and `therapy` views show physical values.
The importer does not read the `SA2` oximetry files.

A therapy day starts at 12:00 device time.
The device clock uses a fixed UTC offset.
The importer reads `TimeZoneOffset` from `SETTINGS/CurrentSettings.json` on each run.
This offset defines the device therapy dates and the timestamps on a native SD card.
The importer does not use `TZ`.

SomnoTrace exports use its configured timezone for EDF timestamps and mask minutes.
Its therapy dates still follow the device offset.
For SomnoTrace exports, set `EXPORT_TIME_ZONE` to its configured IANA zone, such as `America/Chicago`.
The importer applies seasonal clock changes only to that export timezone.
The device offset remains fixed.

EDF timestamps do not contain a standard timezone field.
`CurrentSettings.json` describes the device offset, not the SomnoTrace export timezone.
Historical files require the export timezone that applied when SomnoTrace wrote them.
For archives from different clock configurations, use separate databases.
Ambiguous or nonexistent source timestamps fail the file import; EDF cannot identify the repeated hour.

At `-05:00`, device noon is 17:00 UTC throughout the year.
In Chicago, that is noon during daylight time and 11:00 during standard time.
The importer stores UTC timestamps. Grafana converts them to the display zone.

## Run

```sh
docker run --rm \
  --volume /path/to/sd-card:/data/input:ro \
  --env INPUT_DIR=/data/input \
  --env PGHOST=db.example --env PGDATABASE=sleep_ingest \
  --env PGUSER=sleep_ingest --env PGPASSWORD=secret \
  tmmrtn/sleep-ingest
```

| Option | Environment variable | Default |
|---|---|---|
| `--data-path` | `INPUT_DIR` | None. One of the two is necessary. |
| `--database-url` | `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | The libpq defaults. |
| `--time-zone` | | `TimeZoneOffset` in `SETTINGS/CurrentSettings.json`. |
| `--export-time-zone` | `EXPORT_TIME_ZONE` | None. Native device timestamps. |
| `--rebuild` | | False. |

To override the device offset, use `--time-zone=-05:00`.
The option accepts only fixed UTC offsets.
For SomnoTrace exports, also give `--export-time-zone=America/Chicago` or set `EXPORT_TIME_ZONE`.

The importer does not write to the data path.
For each file with new records or an error, it writes one line to stderr, for example `[12/383] DATALOG/20261102/20261103_004817_PLD.edf: 15720 rows in therapy_raw`.
At the end, it writes a summary to stdout.

| Exit code | Meaning |
|---|---|
| 0 | All files imported. |
| 1 | One or more files failed. The other files imported. |
| 2 | The importer stopped because of an option, the data path, the time zone, or the database. |

## Duplicates

You can run the importer again on the same files at any time.
The `source_file` table holds the number of complete records that the importer read from each `DATALOG` file.
Each run imports only the records after that number, so a file that the device still writes imports in parts.
The rows of a file and its new record count commit in one transaction.

Primary keys also prevent duplicates.
The importer does not change existing rows in the time series tables.
It updates `daily_summary` and `mask_session` rows when `STR.edf` changes.
It removes obsolete mask sessions for each updated summary day.
If mask intervals exceed the reported duration, the importer retains the summary and omits those intervals.
It allows one minute of rounding error per closed interval.
The importer reports unavailable mask times on stderr; it does not invent replacement times.

## Rebuild

Earlier versions used one timezone for device therapy dates and source timestamps.
SomnoTrace exports require separate device and export timezones.
A normal import does not correct existing time series rows.
The importer records both clocks in `import_clock` and rejects unknown or different clocks in existing data.

Stop scheduled imports before a rebuild.
Back up the database.
Use a complete card archive that includes all previously imported files and summary days.
Run the importer with `--rebuild` and the correct device offset and export timezone.

```sh
sleep-ingest --data-path /path/to/somnotrace/archive --time-zone=-05:00 --export-time-zone=America/Chicago --rebuild
```

The rebuild replaces all imported rows in one transaction.
If any file fails, the database retains its previous rows.
The importer rejects archives that lack known files, records, or summary days.
It cannot detect data that earlier imports never recorded.
A rebuild holds table locks until it finishes.

## Schema

The importer creates the tables and views at start.
It adds `daily_summary.day_start` for the UTC start of each device therapy day.
Existing data requires a rebuild before the updated trends dashboard can use this column.

## Dashboards

| File | Contents |
|---|---|
| [`dashboards/trends.json`](dashboards/trends.json) | Nightly usage, AHI, leak, pressure, and breathing, with a table of nights and setting changes. Mask-on times for each night and a sleep regularity index. |
| [`dashboards/night.json`](dashboards/night.json) | One night: flow rate, pressure, leak, flow limitation, snore, and breathing, with events as annotations. |

The dashboards use a PostgreSQL datasource with the UID `sleep_ingest_timescaledb`.
Both dashboards use the Grafana display timezone, which defaults to the browser timezone.
The time picker controls this timezone.
The trends dashboard uses stored UTC day boundaries for daily summaries.
The therapy times panel uses local noon boundaries in the display timezone.
Sessions that cross local noon appear in both columns.
Overlapping intervals and repeated clock minutes count once.

## Development

Start a TimescaleDB container for the tests:

```sh
docker compose up --detach --wait
```

Run the checks:

```sh
uv run ruff check
uv run ruff format --check
uv run ty check
TEST_DATABASE_URL=postgresql://sleep_ingest:sleep_ingest@127.0.0.1:55433/sleep_ingest uv run pytest
```

Without `TEST_DATABASE_URL`, pytest skips the database tests.
The tests make small synthetic EDF files.
They do not need real device data.

Each push to `master` runs the checks and then publishes the image to Docker Hub.
