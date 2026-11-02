"""Throwaway generator for dashboards/*.json. Delete after use."""

import json
from pathlib import Path

DS = {"type": "grafana-postgresql-datasource", "uid": "sleep_ingest_timescaledb"}
TZ = "${TimeZone:sqlstring}"
NOON = f"(day + time '12:00') AT TIME ZONE {TZ}"
IN_RANGE = f"{NOON} BETWEEN $__timeFrom() AND $__timeTo()"
NIGHT_URL = (
    "/d/sleep-night/sleep-night?from=${__data.fields.from}&to=${__data.fields.to}"
)

BLUE, ORANGE, AQUA = "#3987e5", "#d95926", "#199e70"
RAMP = ("#86b6ef", "#3987e5", "#1c5cab")  # median, 95th percentile, maximum
OBSTRUCTIVE, HYPOPNEA, CENTRAL, OTHER = "#d55181", "#9085e9", "#008300", "#898781"
THRESHOLD = "#898781"

LINKS = [
    {
        "asDropdown": False,
        "icon": "external link",
        "includeVars": False,
        "keepTime": False,
        "tags": ["sleep"],
        "targetBlank": False,
        "title": "Sleep",
        "type": "dashboards",
    }
]


class Layout:
    def __init__(self):
        self.x = self.y = self.row_h = 0
        self.next_id = 1

    def place(self, w, h):
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_h
            self.row_h = 0
        pos = {"h": h, "w": w, "x": self.x, "y": self.y}
        self.x += w
        self.row_h = max(self.row_h, h)
        self.next_id += 1
        return self.next_id - 1, pos


def target(sql, fmt):
    return {
        "refId": "A",
        "datasource": DS,
        "editorMode": "code",
        "rawQuery": True,
        "rawSql": sql,
        "format": fmt,
    }


def color(hex_):
    return {"mode": "fixed", "fixedColor": hex_}


def series_colors(colors):
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": color(hex_)}],
        }
        for name, hex_ in colors.items()
    ]


def stat(
    layout, title, sql, unit, w=4, decimals=1, description=None, text_mode="value"
):
    pid, pos = layout.place(w, 4)
    panel = {
        "id": pid,
        "gridPos": pos,
        "type": "stat",
        "title": title,
        "datasource": DS,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "fixed", "fixedColor": "text"},
                "decimals": decimals,
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "none",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showPercentChange": False,
            "textMode": text_mode,
            "wideLayout": True,
        },
        "targets": [target(sql, "table")],
    }
    if description:
        panel["description"] = description
    return panel


def timeseries(
    layout,
    title,
    sql,
    unit,
    *,
    w=24,
    h=8,
    colors=None,
    bars=False,
    stacked=False,
    threshold=None,
    interval=None,
    description=None,
    overrides=(),
    minimum=0,
):
    pid, pos = layout.place(w, h)
    colors = colors or {}
    custom = {
        "drawStyle": "bars" if bars else "line",
        "lineWidth": 1 if bars else 2,
        "fillOpacity": 80 if bars else 0,
        "barAlignment": 0,
        "gradientMode": "none",
        "showPoints": "never",
        "spanNulls": False,
        "axisBorderShow": False,
        "axisPlacement": "auto",
        "stacking": {"group": "A", "mode": "normal" if stacked else "none"},
        "thresholdsStyle": {"mode": "dashed" if threshold is not None else "off"},
    }
    steps = [{"color": "transparent", "value": None}]
    if threshold is not None:
        steps.append({"color": THRESHOLD, "value": threshold})
    defaults = {
        "color": color(BLUE),
        "custom": custom,
        "thresholds": {"mode": "absolute", "steps": steps},
        "unit": unit,
    }
    if minimum is not None:
        defaults["min"] = minimum
    panel = {
        "id": pid,
        "gridPos": pos,
        "type": "timeseries",
        "title": title,
        "datasource": DS,
        "fieldConfig": {
            "defaults": defaults,
            "overrides": series_colors(colors) + list(overrides),
        },
        "options": {
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": len(colors) > 1,
                "calcs": [],
            },
            "tooltip": {"mode": "multi", "sort": "none"},
        },
        "targets": [target(sql, "time_series")],
    }
    if interval:
        panel["interval"] = interval
    if description:
        panel["description"] = description
    return panel


def table(layout, title, sql, *, h=10, w=24, overrides=(), description=None, sort=None):
    pid, pos = layout.place(w, h)
    hidden = [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "custom.hidden", "value": True}],
        }
        for name in ("from", "to")
    ]
    panel = {
        "id": pid,
        "gridPos": pos,
        "type": "table",
        "title": title,
        "datasource": DS,
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
            "overrides": list(overrides) + hidden,
        },
        "options": {"showHeader": True, "cellHeight": "sm", "sortBy": sort or []},
        "targets": [target(sql, "table")],
    }
    if description:
        panel["description"] = description
    return panel


def field(name, **properties):
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [
            {"id": key, "value": value} for key, value in properties.items()
        ],
    }


def link(name, title, url):
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [{"id": "links", "value": [{"title": title, "url": url}]}],
    }


def dashboard(uid, title, time, panels, *, variables=(), annotations=(), description):
    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "Annotations & Alerts",
                    "type": "dashboard",
                },
                *annotations,
            ]
        },
        "description": description,
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": LINKS,
        "panels": panels,
        "refresh": "",
        "schemaVersion": 41,
        "tags": ["sleep"],
        "templating": {"list": list(variables)},
        "time": time,
        "timepicker": {},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "",
    }


def trends():
    layout = Layout()
    daily = f"FROM daily_summary\nWHERE {IN_RANGE}"
    panels = [
        stat(
            layout,
            "Nights used",
            f'SELECT count(*) FILTER (WHERE duration_min > 0) AS "Nights"\n{daily}',
            "none",
            decimals=0,
        ),
        stat(
            layout,
            "Compliance",
            "SELECT 100.0 * count(*) FILTER (WHERE duration_min >= 240)"
            f' / NULLIF(max(day) - min(day) + 1, 0) AS "Compliance"\n{daily}',
            "percent",
            decimals=0,
            description="Nights with 4 hours or more of use, as a percentage of the"
            " days from the first to the last night in the range.",
        ),
        stat(
            layout,
            "Usage per night",
            "SELECT avg(duration_min) FILTER (WHERE duration_min > 0) / 60.0"
            f' AS "Usage"\n{daily}',
            "h",
            description="Average of the nights with use.",
        ),
        stat(
            layout,
            "AHI",
            "SELECT sum(ahi * duration_min) / NULLIF(sum(duration_min), 0)"
            f' AS "AHI"\n{daily}',
            "suffix: /h",
            description="Apneas and hypopneas for each hour of use, for all nights"
            " in the range.",
        ),
        stat(
            layout,
            "Leak, 95th percentile",
            f'SELECT avg(leak_95) * 60 AS "Leak"\n{daily}',
            "suffix: L/min",
            description="Average of the nightly values.",
        ),
        stat(
            layout,
            "Pressure, 95th percentile",
            f'SELECT avg(mask_press_95) AS "Pressure"\n{daily}',
            "suffix: cmH2O",
            description="Average of the nightly mask pressure values.",
        ),
        timeseries(
            layout,
            "Usage",
            f'SELECT {NOON} AS time, duration_min / 60.0 AS "Usage"\n{daily}\nORDER BY 1',
            "h",
            w=12,
            bars=True,
            threshold=4,
            description="Hours of use for each night. The dashed line is 4 hours.",
        ),
        timeseries(
            layout,
            "AHI",
            f"SELECT {NOON} AS time,\n"
            '  oai AS "Obstructive", hi AS "Hypopnea", cai AS "Central",'
            ' uai AS "Unclassified"\n'
            f"{daily}\nORDER BY 1",
            "suffix: /h",
            w=12,
            bars=True,
            stacked=True,
            threshold=5,
            colors={
                "Obstructive": OBSTRUCTIVE,
                "Hypopnea": HYPOPNEA,
                "Central": CENTRAL,
                "Unclassified": OTHER,
            },
            description="Events for each hour of use, by type, as the device"
            " reports them. The dashed line is 5 events per hour.",
        ),
        timeseries(
            layout,
            "Leak",
            f"SELECT {NOON} AS time,\n"
            '  leak_50 * 60 AS "Median", leak_95 * 60 AS "95th percentile",'
            ' leak_max * 60 AS "Maximum"\n'
            f"{daily}\nORDER BY 1",
            "suffix: L/min",
            w=12,
            threshold=24,
            colors=dict(zip(("Median", "95th percentile", "Maximum"), RAMP)),
            description="Unintended leak. The dashed line is 24 L/min, the ResMed"
            " limit for a large leak.",
        ),
        timeseries(
            layout,
            "Mask pressure",
            f"SELECT {NOON} AS time,\n"
            '  mask_press_50 AS "Median", mask_press_95 AS "95th percentile",'
            ' mask_press_max AS "Maximum"\n'
            f"{daily}\nORDER BY 1",
            "suffix: cmH2O",
            w=12,
            colors=dict(zip(("Median", "95th percentile", "Maximum"), RAMP)),
        ),
        timeseries(
            layout,
            "Breathing rate, median",
            f'SELECT {NOON} AS time, resp_rate_50 AS "Breathing rate"\n{daily}\nORDER BY 1',
            "suffix: /min",
            w=8,
            minimum=None,
        ),
        timeseries(
            layout,
            "Tidal volume, median",
            f'SELECT {NOON} AS time, tid_vol_50 * 1000 AS "Tidal volume"\n{daily}\nORDER BY 1',
            "suffix: mL",
            w=8,
            minimum=None,
        ),
        timeseries(
            layout,
            "Minute ventilation, median",
            f'SELECT {NOON} AS time, min_vent_50 AS "Minute ventilation"\n{daily}\nORDER BY 1',
            "suffix: L/min",
            w=8,
            minimum=None,
        ),
        table(
            layout,
            "Nights",
            'SELECT d.day AS "Night",\n'
            '  extract(epoch FROM min(s.mask_on)) * 1000 AS "Mask on",\n'
            '  extract(epoch FROM max(coalesce(s.mask_off, s.mask_on))) * 1000 AS "Mask off",\n'
            '  count(s.slot) AS "Sessions",\n'
            '  d.duration_min / 60.0 AS "Usage",\n'
            '  d.ahi AS "AHI",\n'
            '  d.leak_95 * 60 AS "Leak 95%",\n'
            '  d.mask_press_95 AS "Pressure 95%",\n'
            "  extract(epoch FROM min(s.mask_on) - interval '15 minutes') * 1000 AS \"from\",\n"
            "  extract(epoch FROM max(coalesce(s.mask_off, s.mask_on)) + interval '15 minutes')"
            ' * 1000 AS "to"\n'
            "FROM daily_summary d LEFT JOIN mask_session s USING (day)\n"
            f"WHERE (d.day + time '12:00') AT TIME ZONE {TZ} BETWEEN $__timeFrom() AND $__timeTo()\n"
            "GROUP BY d.day\nORDER BY d.day DESC",
            h=12,
            description="Select a night to open it in the night dashboard.",
            overrides=[
                link("Night", "Open the night", NIGHT_URL),
                field("Mask on", unit="time:HH:mm"),
                field("Mask off", unit="time:HH:mm"),
                field("Usage", unit="h", decimals=1),
                field("AHI", decimals=1),
                field("Leak 95%", unit="suffix: L/min", decimals=1),
                field("Pressure 95%", unit="suffix: cmH2O", decimals=1),
            ],
        ),
        table(
            layout,
            "Setting changes",
            'SELECT day AS "Night", key AS "Setting",\n'
            '  prev ->> key AS "Before", settings ->> key AS "After"\n'
            "FROM (\n"
            "  SELECT day, settings, lag(settings) OVER (ORDER BY day) AS prev\n"
            "  FROM daily_summary\n"
            ") d, jsonb_object_keys(settings) AS key\n"
            "WHERE prev IS NOT NULL AND prev -> key IS DISTINCT FROM settings -> key\n"
            f"  AND {IN_RANGE}\n"
            "ORDER BY day DESC, key",
            h=8,
            description="Device settings that differ from the night before. The"
            " values are device codes.",
        ),
    ]
    variable = {
        "current": {"text": "America/Chicago", "value": "America/Chicago"},
        "description": "Time zone of the device clock. It places each night on its date.",
        "label": "Time zone",
        "name": "TimeZone",
        "query": "America/Chicago",
        "type": "textbox",
    }
    return dashboard(
        "sleep-trends",
        "Sleep trends",
        {"from": "now-90d", "to": "now"},
        panels,
        variables=[variable],
        description="Nightly CPAP summaries from the ResMed AirSense 11.",
    )


def night():
    layout = Layout()
    days = (
        "WHERE day IN (\n"
        "  SELECT day FROM mask_session\n"
        "  WHERE mask_on < $__timeTo() AND coalesce(mask_off, mask_on) > $__timeFrom()\n"
        ")\nORDER BY day DESC\nLIMIT 1"
    )
    last = "The last night that has mask time in the range."
    bucket = "$__timeGroupAlias(time, $__interval, NULL)"
    panels = [
        stat(
            layout,
            "Usage",
            f'SELECT duration_min / 60.0 AS "Usage" FROM daily_summary\n{days}',
            "h",
            w=3,
            description=last,
        ),
        stat(
            layout,
            "AHI",
            f'SELECT ahi AS "AHI" FROM daily_summary\n{days}',
            "suffix: /h",
            w=3,
            description=last,
        ),
        stat(
            layout,
            "Events",
            "SELECT\n"
            "  count(*) FILTER (WHERE kind = 'Obstructive Apnea') AS \"Obstructive\",\n"
            "  count(*) FILTER (WHERE kind = 'Hypopnea') AS \"Hypopnea\",\n"
            "  count(*) FILTER (WHERE kind = 'Central Apnea') AS \"Central\",\n"
            "  count(*) FILTER (WHERE kind = 'Apnea') AS \"Unclassified\",\n"
            "  count(*) FILTER (WHERE kind = 'Arousal') AS \"Arousal\"\n"
            "FROM event\nWHERE $__timeFilter(time)",
            "none",
            w=9,
            decimals=0,
            text_mode="value_and_name",
            description="Events in the time range.",
        ),
        stat(
            layout,
            "Leak 95%",
            f'SELECT leak_95 * 60 AS "Leak" FROM daily_summary\n{days}',
            "suffix: L/min",
            w=3,
            description=last,
        ),
        stat(
            layout,
            "Large leak",
            "SELECT 100.0 * count(*) FILTER (WHERE leak > 0.4) / NULLIF(count(leak), 0)"
            ' AS "Large leak"\nFROM therapy\nWHERE $__timeFilter(time)',
            "percent",
            w=3,
            description="Time with a leak above 24 L/min, as a percentage of the"
            " recorded time in the range.",
        ),
        stat(
            layout,
            "Pressure 95%",
            f'SELECT mask_press_95 AS "Pressure" FROM daily_summary\n{days}',
            "suffix: cmH2O",
            w=3,
            description=last,
        ),
        timeseries(
            layout,
            "Flow rate",
            f'SELECT {bucket},\n  min(flow) * 60 AS "Minimum", max(flow) * 60 AS "Maximum"\n'
            "FROM waveform\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "suffix: L/min",
            h=9,
            interval="40ms",
            minimum=None,
            description="The band spans the lowest and highest flow in each time"
            " interval. Zoom in to see each breath.",
            overrides=[
                field(
                    "Maximum",
                    **{"custom.fillBelowTo": "Minimum", "custom.fillOpacity": 40},
                ),
            ],
        ),
        timeseries(
            layout,
            "Pressure",
            f"SELECT {bucket},\n"
            '  avg(mask_press) AS "Mask", avg(press) AS "Target", avg(epr_press) AS "EPR"\n'
            "FROM therapy\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "suffix: cmH2O",
            interval="2s",
            colors={"Mask": BLUE, "Target": ORANGE, "EPR": AQUA},
        ),
        timeseries(
            layout,
            "Leak",
            f'SELECT {bucket}, avg(leak) * 60 AS "Leak"\n'
            "FROM therapy\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "suffix: L/min",
            interval="2s",
            threshold=24,
            description="Unintended leak. The dashed line is 24 L/min, the ResMed"
            " limit for a large leak.",
        ),
        timeseries(
            layout,
            "Flow limitation",
            f'SELECT {bucket}, avg(flow_lim) AS "Flow limitation"\n'
            "FROM therapy\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "none",
            w=12,
            interval="2s",
        ),
        timeseries(
            layout,
            "Snore",
            f'SELECT {bucket}, avg(snore) AS "Snore"\n'
            "FROM therapy\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "none",
            w=12,
            interval="2s",
        ),
        timeseries(
            layout,
            "Breathing rate",
            f'SELECT {bucket}, avg(resp_rate) AS "Breathing rate"\n'
            "FROM therapy\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "suffix: /min",
            w=8,
            interval="2s",
        ),
        timeseries(
            layout,
            "Tidal volume",
            f'SELECT {bucket}, avg(tid_vol) * 1000 AS "Tidal volume"\n'
            "FROM therapy\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "suffix: mL",
            w=8,
            interval="2s",
        ),
        timeseries(
            layout,
            "Minute ventilation",
            f'SELECT {bucket}, avg(min_vent) AS "Minute ventilation"\n'
            "FROM therapy\nWHERE $__timeFilter(time)\nGROUP BY 1\nORDER BY 1",
            "suffix: L/min",
            w=8,
            interval="2s",
        ),
        table(
            layout,
            "Events",
            'SELECT time AS "Time", kind AS "Event", duration_s AS "Duration",\n'
            "  extract(epoch FROM time - interval '90 seconds') * 1000 AS \"from\",\n"
            "  extract(epoch FROM time + interval '90 seconds') * 1000 AS \"to\"\n"
            "FROM event\nWHERE $__timeFilter(time)\nORDER BY time",
            w=12,
            description="Select an event to zoom to the 3 minutes around it.",
            overrides=[
                link("Event", "Zoom to the event", NIGHT_URL),
                field("Duration", unit="s"),
            ],
        ),
        table(
            layout,
            "Mask sessions",
            'SELECT mask_on AS "Mask on", mask_off AS "Mask off",\n'
            '  extract(epoch FROM mask_off - mask_on) AS "Duration"\n'
            "FROM mask_session\n"
            "WHERE mask_on < $__timeTo() AND coalesce(mask_off, mask_on) > $__timeFrom()\n"
            "ORDER BY mask_on",
            w=12,
            overrides=[field("Duration", unit="dtdurations")],
        ),
    ]
    annotations = [
        {
            "datasource": DS,
            "enable": True,
            "iconColor": hex_,
            "name": name,
            "target": target(
                "SELECT time, time + make_interval(secs => duration_s) AS timeend,"
                " kind AS text\n"
                f"FROM event\nWHERE $__timeFilter(time) AND kind = '{kind}'",
                "table",
            ),
        }
        for name, kind, hex_ in (
            ("Obstructive apnea", "Obstructive Apnea", OBSTRUCTIVE),
            ("Hypopnea", "Hypopnea", HYPOPNEA),
            ("Central apnea", "Central Apnea", CENTRAL),
            ("Unclassified apnea", "Apnea", OTHER),
            ("Arousal", "Arousal", OTHER),
        )
    ]
    return dashboard(
        "sleep-night",
        "Sleep night",
        {"from": "now-24h", "to": "now"},
        panels,
        annotations=annotations,
        description="Detailed CPAP data for one night from the ResMed AirSense 11.",
    )


out = Path(__file__).resolve().parent.parent / "dashboards"
out.mkdir(exist_ok=True)
for name, board in (("trends", trends()), ("night", night())):
    (out / f"{name}.json").write_text(json.dumps(board, indent=2) + "\n")
