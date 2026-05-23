import pytest


def _schema(*fields):
    pa = pytest.importorskip("pyarrow")
    return pa.schema(fields)


def test_integer_column_maps_to_integer():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("count", pa.int64()))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "integer"


def test_float_column_maps_to_float():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("amount", pa.float64()))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "float"


def test_boolean_column_maps_to_boolean():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("active", pa.bool_()))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "boolean"


def test_date_column_maps_to_date():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("created_at", pa.date32()))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "date"


def test_timestamp_column_maps_to_timestamp():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("ts", pa.timestamp("us")))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "timestamp"


def test_string_column_maps_to_string():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("name", pa.string()))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "string"


def test_all_columns_are_high_confidence():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("id", pa.int64()), pa.field("name", pa.string()))
    cols = infer_schema_from_parquet(schema)
    assert all(c.confidence == "high" for c in cols)


def test_high_confidence_has_schema_note():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("id", pa.int64()))
    cols = infer_schema_from_parquet(schema)
    assert any("schema" in note.lower() for note in cols[0].notes)


def test_nested_struct_becomes_string_with_note():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    struct_type = pa.struct([pa.field("city", pa.string()), pa.field("zip", pa.string())])
    schema = _schema(pa.field("address", struct_type))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "string"
    assert any("nested" in note.lower() for note in cols[0].notes)


def test_list_column_becomes_string_with_note():
    pa = pytest.importorskip("pyarrow")
    from filedge.inferrer import infer_schema_from_parquet
    schema = _schema(pa.field("tags", pa.list_(pa.string())))
    cols = infer_schema_from_parquet(schema)
    assert cols[0].inferred_type == "string"
    assert any("array" in note.lower() for note in cols[0].notes)
