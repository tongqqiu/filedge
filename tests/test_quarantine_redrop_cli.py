"""The `filedge redrop-quarantine` Operator CLI command turns a quarantine
sidecar into a clean, re-droppable NDJSON File — with a sensible default output
path, a loud failure on a malformed sidecar, and an NDJSON-compatibility warning
when checked against a non-NDJSON Pipeline.
"""

import json

from click.testing import CliRunner

from filedge.cli import cli


def _write_sidecar(path, *records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _sidecar_records():
    return (
        {"row_number": 2, "column": "amount", "error": "not a float",
         "row": {"id": "2", "amount": "n/a"}},
        {"row_number": 5, "column": "id", "error": "not an integer",
         "row": {"id": "x", "amount": "3.5"}},
    )


def test_redrop_writes_clean_ndjson_to_default_path(tmp_path):
    sidecar = tmp_path / "orders.abc123.quarantine.ndjson"
    _write_sidecar(sidecar, *_sidecar_records())

    result = CliRunner().invoke(cli, ["redrop-quarantine", "--sidecar", str(sidecar)])

    assert result.exit_code == 0, result.output
    out = tmp_path / "orders.abc123.redrop.ndjson"
    assert out.exists()
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows == [{"id": "2", "amount": "n/a"}, {"id": "x", "amount": "3.5"}]
    # Diagnostics never appear in the re-dropped File.
    assert "row_number" not in out.read_text()
    assert "error" not in out.read_text()


def test_redrop_honors_explicit_output_path(tmp_path):
    sidecar = tmp_path / "data.quarantine.ndjson"
    _write_sidecar(sidecar, *_sidecar_records())
    out = tmp_path / "fixed" / "corrected.ndjson"
    out.parent.mkdir()

    result = CliRunner().invoke(
        cli, ["redrop-quarantine", "--sidecar", str(sidecar), "--output", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert str(out) in result.output


def test_redrop_fails_loudly_on_malformed_sidecar_and_leaves_no_file(tmp_path):
    sidecar = tmp_path / "broken.quarantine.ndjson"
    sidecar.write_text(
        json.dumps({"row_number": 1, "column": "a", "error": "e", "row": {"a": "1"}}) + "\n"
        "{not valid json}\n"
    )

    result = CliRunner().invoke(cli, ["redrop-quarantine", "--sidecar", str(sidecar)])

    assert result.exit_code == 1
    assert "Error" in result.output
    # No partial output File is left behind.
    assert not (tmp_path / "broken.redrop.ndjson").exists()


def test_redrop_errors_on_missing_sidecar(tmp_path):
    result = CliRunner().invoke(
        cli, ["redrop-quarantine", "--sidecar", str(tmp_path / "nope.ndjson")]
    )
    assert result.exit_code != 0


def test_redrop_warns_when_pipeline_is_not_ndjson(tmp_path):
    sidecar = tmp_path / "data.quarantine.ndjson"
    _write_sidecar(sidecar, *_sidecar_records())
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "format: csv\n"
        "dest_table: items\n"
        "connector:\n  type: sqlite\n  url: sqlite:///x.db\n"
        "columns:\n  - source: id\n    dest: id\n    type: string\n"
    )

    result = CliRunner().invoke(
        cli, ["redrop-quarantine", "--sidecar", str(sidecar), "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    assert "Warning" in result.output
    assert "NDJSON" in result.output


def test_redrop_no_warning_for_ndjson_pipeline(tmp_path):
    sidecar = tmp_path / "data.quarantine.ndjson"
    _write_sidecar(sidecar, *_sidecar_records())
    config = tmp_path / "pipeline.yaml"
    config.write_text(
        "format: ndjson\n"
        "dest_table: items\n"
        "connector:\n  type: sqlite\n  url: sqlite:///x.db\n"
        "columns:\n  - source: id\n    dest: id\n    type: string\n"
    )

    result = CliRunner().invoke(
        cli, ["redrop-quarantine", "--sidecar", str(sidecar), "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    assert "Warning" not in result.output
