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
The device clock shows local time, but the files do not give its time zone.
The `TimeZoneOffset` in `SETTINGS/CurrentSettings.json` does not change for daylight saving time, so the importer does not use it.

## Run

```sh
docker run --rm \
  --volume /path/to/sd-card:/data/input:ro \
  --env INPUT_DIR=/data/input \
  --env TZ=America/Chicago \
  --env PGHOST=db.example --env PGDATABASE=sleep_ingest \
  --env PGUSER=sleep_ingest --env PGPASSWORD=secret \
  tmmrtn/sleep-ingest
```

| Option | Environment variable | Default |
|---|---|---|
| `--data-path` | `INPUT_DIR` | None. One of the two is necessary. |
| `--database-url` | `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | The libpq defaults. |
| `--time-zone` | `TZ` | None. One of the two is necessary. |

`--time-zone` accepts an IANA zone name, for example `America/Chicago`, or a UTC offset, for example `-05:00`.
Use a zone name to apply daylight saving time.
The device can change its clock later than the time change. Then, some times on that night are one hour wrong.

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

## Schema

The importer creates the tables and views at start.
There are no migrations.
To change the schema, drop the tables and import the files again.

## Dashboards

| File | Contents |
|---|---|
| [`dashboards/trends.json`](dashboards/trends.json) | Nightly usage, AHI, leak, pressure, and breathing, with a table of nights and setting changes. |
| [`dashboards/night.json`](dashboards/night.json) | One night: flow rate, pressure, leak, flow limitation, snore, and breathing, with events as annotations. |

The dashboards use a PostgreSQL datasource with the UID `sleep_ingest_timescaledb`.
The trends dashboard has a `TimeZone` variable that places each night on its date.

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
