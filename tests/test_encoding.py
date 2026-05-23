"""Tests for encoding support across inspect, preview, validate, and run."""

from click.testing import CliRunner

from filedge.cli import cli


def _ebcdic_csv(text: str, codec: str = "cp500") -> bytes:
    return text.encode(codec)


def _write_ebcdic(path, text: str, codec: str = "cp500"):
    path.write_bytes(_ebcdic_csv(text, codec))


def _run(*args):
    return CliRunner().invoke(cli, list(args))


# --- inspect ------------------------------------------------------------------

def test_inspect_encoding_flag_reads_ebcdic(tmp_path):
    f = tmp_path / "data.csv"
    _write_ebcdic(f, "name,amount\nAlice,9.99\nBob,1.50\n")
    result = _run("inspect", str(f), "--encoding", "cp500")
    assert result.exit_code == 0
    assert "name" in result.output
    assert "amount" in result.output


def test_inspect_encoding_flag_default_utf8_fails_on_ebcdic(tmp_path):
    f = tmp_path / "data.csv"
    _write_ebcdic(f, "name,amount\nAlice,9.99\n")
    result = _run("inspect", str(f))
    assert result.exit_code != 0


# --- preview ------------------------------------------------------------------

def test_preview_encoding_flag_reads_ebcdic(tmp_path):
    f = tmp_path / "data.csv"
    _write_ebcdic(f, "name,amount\nAlice,9.99\nBob,1.50\n")
    result = _run("preview", str(f), "--encoding", "cp500")
    assert result.exit_code == 0
    assert "Alice" in result.output


# --- validate -----------------------------------------------------------------

def test_validate_encoding_from_config(tmp_path):
    f = tmp_path / "data.csv"
    _write_ebcdic(f, "name,amount\nAlice,9.99\n")
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: csv\ndest_table: t\nencoding: cp500\n"
        "connector:\n  type: sqlite\n  url: sqlite:///ignored.db\n"
        "columns:\n"
        "  - source: name\n    dest: name\n    type: string\n    required: true\n"
        "  - source: amount\n    dest: amount\n    type: float\n    required: true\n"
    )
    result = _run("validate", str(f), "--config", str(cfg))
    assert result.exit_code == 0


def test_validate_encoding_flag_overrides_config(tmp_path):
    f = tmp_path / "data.csv"
    _write_ebcdic(f, "name,amount\nAlice,9.99\n", "cp500")
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(
        "format: csv\ndest_table: t\n"
        "connector:\n  type: sqlite\n  url: sqlite:///ignored.db\n"
        "columns:\n"
        "  - source: name\n    dest: name\n    type: string\n    required: true\n"
    )
    result = _run("validate", str(f), "--config", str(cfg), "--encoding", "cp500")
    assert result.exit_code == 0
