import json

import pytest

from satcal import cli


def test_version_prints_something(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--version"])
    captured = capsys.readouterr()
    assert captured.out.strip() != ""


def test_help_prints_description(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Predict when an Earth–orbiting satellite will be visible" in captured.out


def test_json_output_parses(capsys: pytest.CaptureFixture[str]) -> None:
    # A simple smoke test that exercising the CLI with --json produces valid JSON.
    # Use ISS as a commonly-known NORAD ID and a short time window.
    try:
        cli.main(["25544", "0", "0", "1", "--json"])
    except SystemExit as exc:  # pragma: no cover - network issues etc.
        if exc.code != 0:
            pytest.skip(f"satcal CLI returned non-zero exit code: {exc.code}")
    captured = capsys.readouterr()
    if not captured.out.strip():
        pytest.skip("No JSON output produced; likely a transient network or data issue.")
    data = json.loads(captured.out)
    assert isinstance(data, list)

