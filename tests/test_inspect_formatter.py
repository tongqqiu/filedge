import yaml

from filedge.inferrer import InferredColumn
from filedge.inspect_formatter import format_summary, format_yaml


def col(name, inferred_type="string", confidence="high", null_count=0, total_seen=3, notes=None):
    return InferredColumn(name, inferred_type, confidence, null_count, total_seen, notes or [])


# --- format_yaml ---

def test_yaml_is_valid_and_parseable():
    columns = [col("amount", "float"), col("name", "string")]
    output = format_yaml(columns, source_path="data.csv", sample_rows=1000)
    parsed = yaml.safe_load(output)
    assert "columns" in parsed


def test_yaml_contains_required_fields():
    columns = [col("amount", "integer")]
    parsed = yaml.safe_load(format_yaml(columns, "data.csv", 1000))
    entry = parsed["columns"][0]
    assert entry["source"] == "amount"
    assert entry["dest"] == "amount"
    assert entry["type"] == "integer"
    assert entry["required"] is True


def test_yaml_required_false_when_nulls():
    columns = [col("amount", "integer", null_count=2)]
    parsed = yaml.safe_load(format_yaml(columns, "data.csv", 1000))
    assert parsed["columns"][0]["required"] is False


def test_yaml_comment_block_contains_source_and_sample():
    output = format_yaml([col("x")], source_path="transactions.csv", sample_rows=500)
    assert "transactions.csv" in output
    assert "500" in output


# --- format_summary ---

def test_summary_high_confidence_no_marker():
    summary = format_summary([col("amount", "float", "high")])
    assert "!" not in summary


def test_summary_low_confidence_marked_with_exclamation():
    summary = format_summary([col("amount", "float", "low", null_count=3)])
    assert "!" in summary


def test_summary_ambiguous_confidence_marked_with_exclamation():
    summary = format_summary([col("v", "string", "ambiguous")])
    assert "!" in summary


def test_summary_includes_column_name_and_type():
    summary = format_summary([col("price", "float", "high")])
    assert "price" in summary
    assert "float" in summary


def test_summary_includes_notes():
    c = col("meta", "string", "ambiguous", notes=["nested object — keys: a, b"])
    summary = format_summary([c])
    assert "nested object" in summary


def test_summary_fits_80_columns():
    long_name = "a" * 40
    summary = format_summary([col(long_name, "string", "high")])
    for line in summary.splitlines():
        assert len(line) <= 80, f"Line too long ({len(line)}): {line!r}"
