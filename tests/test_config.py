
from etl.config import load_config

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
