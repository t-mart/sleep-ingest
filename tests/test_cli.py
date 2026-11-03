import pytest

from sleep_ingest import cli
from tests import synthetic

NO_DATABASE = ["--database-url", "host=/nonexistent"]
ZONE = ["--time-zone", "America/Chicago"]


def test_help_exits_with_zero(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.run(["--help"])

    assert stop.value.code == 0
    assert "--data-path" in capsys.readouterr().out


def test_data_path_is_required(monkeypatch):
    monkeypatch.delenv("INPUT_DIR", raising=False)

    with pytest.raises(SystemExit) as stop:
        cli.run(NO_DATABASE)

    assert stop.value.code == 2


def test_data_path_defaults_to_input_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("INPUT_DIR", str(tmp_path))

    assert cli.run([*ZONE, *NO_DATABASE]) == 2
    assert "Identification.json" in capsys.readouterr().err


def test_time_zone_is_required(monkeypatch, tmp_path):
    monkeypatch.delenv("TZ", raising=False)

    with pytest.raises(SystemExit) as stop:
        cli.run(["--data-path", str(tmp_path), *NO_DATABASE])

    assert stop.value.code == 2


def test_time_zone_defaults_to_tz(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TZ", "Mars/Olympus_Mons")
    root = synthetic.write_card(tmp_path)
    synthetic.write(root, "STR.edf", b"")

    assert cli.run(["--data-path", str(root), *NO_DATABASE]) == 2
    assert "Mars/Olympus_Mons" in capsys.readouterr().err


def test_bad_data_path_stops_before_the_database(tmp_path, capsys):
    root = synthetic.write_card(tmp_path)

    assert cli.run(["--data-path", str(root), *ZONE, *NO_DATABASE]) == 2
    assert "STR.edf is missing" in capsys.readouterr().err


def test_unknown_time_zone_stops_before_the_database(tmp_path, capsys):
    root = synthetic.write_card(tmp_path)
    synthetic.write(root, "STR.edf", b"")

    args = ["--data-path", str(root), "--time-zone", "Mars/Olympus_Mons"]
    assert cli.run([*args, *NO_DATABASE]) == 2
    assert "Mars/Olympus_Mons" in capsys.readouterr().err


def test_report_gives_failures_and_totals(capsys):
    cli.report(
        [
            cli.Outcome("STR.edf", new=True, rows={"daily_summary": 2}),
            cli.Outcome("DATALOG/a_BRP.edf", new=True, rows={"waveform_raw": 3000}),
            cli.Outcome("DATALOG/a_PLD.edf"),
            cli.Outcome("DATALOG/a_EVE.edf", error="bad header"),
        ]
    )

    out, err = capsys.readouterr()
    assert err == "DATALOG/a_EVE.edf: bad header\n"
    assert out == (
        "Files with new records: 2\n"
        "Files without new records: 1\n"
        "Failed files: 1\n"
        "Rows written:\n"
        "  waveform_raw: 3000\n"
        "  therapy_raw: 0\n"
        "  event: 0\n"
        "  daily_summary: 2\n"
        "  mask_session: 0\n"
    )
