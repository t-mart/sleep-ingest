CREATE EXTENSION IF NOT EXISTS timescaledb;

-- BRP files, 25 Hz. Digital values; the view applies the gains.
CREATE TABLE IF NOT EXISTS waveform_raw (
  time  timestamptz PRIMARY KEY,
  flow  smallint,
  press smallint
);
SELECT create_hypertable('waveform_raw', by_range('time', INTERVAL '7 days'), if_not_exists => true);
ALTER TABLE waveform_raw SET (timescaledb.enable_columnstore, timescaledb.orderby = 'time');
CALL add_columnstore_policy('waveform_raw', after => INTERVAL '1 day', if_not_exists => true);

CREATE OR REPLACE VIEW waveform AS
SELECT time,
       flow  * 0.002::float8 AS flow,   -- L/s
       press * 0.02::float8  AS press   -- cmH2O
FROM waveform_raw;

-- PLD files, 0.5 Hz.
CREATE TABLE IF NOT EXISTS therapy_raw (
  time       timestamptz PRIMARY KEY,
  mask_press smallint,
  press      smallint,
  epr_press  smallint,
  leak       smallint,
  resp_rate  smallint,
  tid_vol    smallint,
  min_vent   smallint,
  snore      smallint,
  flow_lim   smallint
);
SELECT create_hypertable('therapy_raw', by_range('time', INTERVAL '30 days'), if_not_exists => true);
ALTER TABLE therapy_raw SET (timescaledb.enable_columnstore, timescaledb.orderby = 'time');
CALL add_columnstore_policy('therapy_raw', after => INTERVAL '1 day', if_not_exists => true);

CREATE OR REPLACE VIEW therapy AS
SELECT time,
       mask_press * 0.02::float8  AS mask_press,  -- cmH2O
       press      * 0.02::float8  AS press,       -- cmH2O
       epr_press  * 0.02::float8  AS epr_press,   -- cmH2O
       leak       * 0.02::float8  AS leak,        -- L/s
       resp_rate  * 0.2::float8   AS resp_rate,   -- breaths/min
       tid_vol    * 0.02::float8  AS tid_vol,     -- L
       min_vent   * 0.125::float8 AS min_vent,    -- L/min
       snore      * 0.02::float8  AS snore,
       flow_lim   * 0.01::float8  AS flow_lim
FROM therapy_raw;

-- EVE and CSL annotations, except 'Recording starts'.
CREATE TABLE IF NOT EXISTS event (
  time       timestamptz NOT NULL,
  kind       text NOT NULL,
  duration_s real NOT NULL,
  PRIMARY KEY (time, kind)
);

-- STR.edf, one row per therapy day.
CREATE TABLE IF NOT EXISTS daily_summary (
  day          date PRIMARY KEY,
  duration_min smallint NOT NULL,
  mask_events  smallint NOT NULL,
  ahi real, hi real, ai real, oai real, cai real, uai real, rin real, csr_min real,
  mask_press_50 real, mask_press_95 real, mask_press_max real,
  tgt_ipap_50 real, tgt_ipap_95 real, tgt_ipap_max real,
  tgt_epap_50 real, tgt_epap_95 real, tgt_epap_max real,
  leak_50 real, leak_70 real, leak_95 real, leak_max real,
  resp_rate_50 real, resp_rate_95 real, resp_rate_max real,
  tid_vol_50 real, tid_vol_95 real, tid_vol_max real,
  min_vent_50 real, min_vent_95 real, min_vent_max real,
  spo2_50 real, spo2_95 real, spo2_max real, spo2_thresh_min real,
  blow_press_5 real, blow_press_95 real, flow_5 real, flow_95 real, blow_flow_50 real,
  amb_humidity_50 real, hum_temp_50 real, htube_temp_50 real, htube_pow_50 real, hum_pow_50 real,
  settings jsonb NOT NULL  -- Mode, S.*, HeatedTube, Humidifier; keys are EDF labels
);

-- STR.edf MaskOn and MaskOff samples. A slot can have mask_on = mask_off.
CREATE TABLE IF NOT EXISTS mask_session (
  day      date NOT NULL REFERENCES daily_summary,
  slot     smallint NOT NULL,  -- sample index in the STR record, 0 to 19
  mask_on  timestamptz NOT NULL,
  mask_off timestamptz,
  PRIMARY KEY (day, slot)
);

-- DATALOG files and the number of records imported from each.
CREATE TABLE IF NOT EXISTS source_file (
  path    text PRIMARY KEY,  -- relative to the data path
  records integer NOT NULL
);
