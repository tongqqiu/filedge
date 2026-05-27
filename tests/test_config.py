
import pytest

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


def test_load_config_parses_field_crypto_blocks(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: csv
dest_table: customers
columns:
  - source: ssn
    dest: ssn_ct
    type: string
    encrypt:
      algorithm: aes-256-gcm
      key: env:DATA_KEY
  - source: ssn
    dest: ssn_join
    type: string
    hash:
      algorithm: hmac-sha256
      key: secrets:/run/secrets/join_key
"""
    )

    config = load_config(str(f))

    assert config.columns[0].encrypt is not None
    assert config.columns[0].encrypt.algorithm == "aes-256-gcm"
    assert config.columns[0].encrypt.key == "env:DATA_KEY"
    assert config.columns[1].hash is not None
    assert config.columns[1].hash.algorithm == "hmac-sha256"
    assert config.columns[1].hash.key == "secrets:/run/secrets/join_key"


def test_load_config_rejects_encrypt_on_non_string_column(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: csv
dest_table: customers
columns:
  - source: customer_id
    dest: customer_id_ct
    type: integer
    encrypt:
      algorithm: aes-256-gcm
      key: env:DATA_KEY
"""
    )

    with pytest.raises(ValueError, match="encrypt.*type: string"):
        load_config(str(f))


def test_load_config_rejects_unknown_field_crypto_algorithm(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: csv
dest_table: customers
columns:
  - source: ssn
    dest: ssn_ct
    type: string
    encrypt:
      algorithm: aes-256-cbc
      key: env:DATA_KEY
"""
    )

    with pytest.raises(ValueError, match="Unsupported encrypt algorithm"):
        load_config(str(f))


def test_load_config_rejects_malformed_field_crypto_key_reference(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: csv
dest_table: customers
columns:
  - source: ssn
    dest: ssn_ct
    type: string
    encrypt:
      algorithm: aes-256-gcm
      key: vault://data-key
"""
    )

    with pytest.raises(ValueError, match="key reference"):
        load_config(str(f))


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


def test_source_manifest_default_is_optional(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_MINIMAL_YAML)
    config = load_config(str(f))
    assert config.source_manifest == "optional"


def test_source_manifest_required(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_MINIMAL_YAML + "\nsource_manifest: required\n")
    config = load_config(str(f))
    assert config.source_manifest == "required"


def test_source_manifest_disabled(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_MINIMAL_YAML + "\nsource_manifest: disabled\n")
    config = load_config(str(f))
    assert config.source_manifest == "disabled"


def test_source_manifest_invalid_value_rejected(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_MINIMAL_YAML + "\nsource_manifest: maybe\n")
    with pytest.raises(ValueError, match="source_manifest"):
        load_config(str(f))


# --- fixed_width layout ---

_FIXED_WIDTH_YAML = """
format: fixed_width
dest_table: transactions
columns:
  - source: account_number
    dest: account_number
    type: string
    required: true
    start: 1
    width: 10
  - source: transaction_date
    dest: transaction_date
    type: date
    required: true
    start: 11
    width: 8
  - source: amount
    dest: amount_cents
    type: integer
    required: true
    start: 19
    width: 12
"""


def test_load_config_parses_fixed_width_layout(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(_FIXED_WIDTH_YAML)
    config = load_config(str(f))
    assert config.format == "fixed_width"
    assert config.columns[0].start == 1
    assert config.columns[0].width == 10
    assert config.columns[2].start == 19
    assert config.columns[2].width == 12


def test_load_config_rejects_overlapping_fixed_width_columns(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: fixed_width
dest_table: transactions
columns:
  - source: account_number
    dest: account_number
    type: string
    start: 1
    width: 10
  - source: branch_code
    dest: branch_code
    type: string
    start: 8
    width: 4
"""
    )
    with pytest.raises(ValueError) as exc_info:
        load_config(str(f))
    msg = str(exc_info.value)
    assert "account_number" in msg and "branch_code" in msg
    assert "overlap" in msg


def test_load_config_rejects_fixed_width_column_missing_start_width(tmp_path):
    f = tmp_path / "pipeline.yaml"
    f.write_text(
        """
format: fixed_width
dest_table: transactions
columns:
  - source: account_number
    dest: account_number
    type: string
"""
    )
    with pytest.raises(ValueError, match="fixed_width.*start.*width"):
        load_config(str(f))


def test_load_config_non_fixed_width_format_ignores_start_width(tmp_path):
    # CSV pipelines never gain start/width — silently absent is fine.
    f = tmp_path / "pipeline.yaml"
    f.write_text(_MINIMAL_YAML)
    config = load_config(str(f))
    assert config.columns[0].start is None
    assert config.columns[0].width is None
