
from filedge.config import load_config

_FULL_YAML = """
format: csv
dest_table: orders
retry_cap: 5
batch_size: 500
stale_timeout_minutes: 15
columns:
  - source: order_id
    dest: order_id
    type: integer
    required: true
  - source: amount
    dest: amount
    type: float
    required: true
  - source: note
    dest: note
    type: string
    required: false
"""

_MINIMAL_YAML = """
format: csv
dest_table: test
columns:
  - source: name
    dest: name
    type: string
"""


def test_load_config_top_level_fields(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_FULL_YAML)
    config = load_config(str(f))

    assert config.format == "csv"
    assert config.dest_table == "orders"
    assert config.retry_cap == 5
    assert config.batch_size == 500
    assert config.stale_timeout_minutes == 15


def test_load_config_columns(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_FULL_YAML)
    config = load_config(str(f))

    assert len(config.columns) == 3
    col = config.columns[0]
    assert col.source == "order_id"
    assert col.dest == "order_id"
    assert col.type == "integer"
    assert col.required is True


def test_load_config_optional_column(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_FULL_YAML)
    config = load_config(str(f))
    assert config.columns[2].required is False


def test_load_config_defaults(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_MINIMAL_YAML)
    config = load_config(str(f))
    assert config.retry_cap == 3
    assert config.batch_size == 1000
    assert config.stale_timeout_minutes == 30
    assert config.encoding == "utf-8"


def test_load_config_encoding(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_MINIMAL_YAML + "encoding: cp500\n")
    config = load_config(str(f))
    assert config.encoding == "cp500"


def test_load_config_cdc_write_mode(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: ndjson
dest_table: customers
write_mode: cdc
cdc:
  keys: [customer_id]
  operation_column: op
  sequence_by: updated_at
  operations:
    insert: [c, insert]
    update: [u, update]
    delete: [d, delete]
columns:
  - source: customer_id
    dest: customer_id
    type: string
    required: true
  - source: email
    dest: email
    type: string
    required: false
  - source: updated_at
    dest: updated_at
    type: timestamp
    required: true
"""
    )

    config = load_config(str(f))

    assert config.write_mode == "cdc"
    assert config.cdc is not None
    assert config.cdc.keys == ["customer_id"]
    assert config.cdc.operation_column == "op"
    assert config.cdc.sequence_by == "updated_at"
    assert config.cdc.operations["insert"] == ["c", "insert"]


def test_load_config_requires_cdc_block_for_cdc_write_mode(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: ndjson
dest_table: customers
write_mode: cdc
columns:
  - source: customer_id
    dest: customer_id
    type: string
    required: true
"""
    )

    try:
        load_config(str(f))
    except ValueError as e:
        assert "write_mode: cdc requires a cdc: block" in str(e)
    else:
        raise AssertionError("Expected load_config to reject missing cdc block")


def test_load_config_requires_cdc_keys_in_columns(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: ndjson
dest_table: customers
write_mode: cdc
cdc:
  keys: [customer_id]
  operation_column: op
  sequence_by: updated_at
  operations:
    insert: [c]
    update: [u]
    delete: [d]
columns:
  - source: updated_at
    dest: updated_at
    type: timestamp
    required: true
"""
    )

    try:
        load_config(str(f))
    except ValueError as e:
        assert "CDC key column 'customer_id' must be declared in columns" in str(e)
    else:
        raise AssertionError("Expected load_config to reject missing CDC key column")


def test_load_config_rejects_unknown_column_type(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: csv
dest_table: test
columns:
  - source: name
    dest: name
    type: money
"""
    )

    try:
        load_config(str(f))
    except ValueError as e:
        assert "Unknown Filedge column type 'money'" in str(e)
    else:
        raise AssertionError("Expected load_config to reject unknown column type")
