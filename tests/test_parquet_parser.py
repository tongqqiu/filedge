import io
import pytest


def _make_parquet_bytes(data: dict) -> io.BytesIO:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    buf = io.BytesIO()
    pq.write_table(pa.table(data), buf)
    buf.seek(0)
    return buf


def test_parquet_parser_yields_dicts():
    pytest.importorskip("pyarrow")
    from filedge.parser import ParquetParser
    f = _make_parquet_bytes({"id": [1, 2, 3], "name": ["Alice", "Bob", "Carol"]})
    rows = list(ParquetParser().parse(f))
    assert len(rows) == 3
    assert rows[0] == {"id": 1, "name": "Alice"}


def test_parquet_parser_mode_is_binary():
    from filedge.parser import ParquetParser
    assert ParquetParser.mode == "rb"


def test_parquet_parser_nested_struct_becomes_string():
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from filedge.parser import ParquetParser
    struct_type = pa.struct([pa.field("city", pa.string()), pa.field("zip", pa.string())])
    data = pa.table({
        "id": pa.array([1, 2]),
        "address": pa.array(
            [{"city": "NYC", "zip": "10001"}, {"city": "LA", "zip": "90001"}],
            type=struct_type,
        ),
    })
    buf = io.BytesIO()
    pq.write_table(data, buf)
    buf.seek(0)
    rows = list(ParquetParser().parse(buf))
    assert isinstance(rows[0]["address"], str)


def test_parquet_parser_list_column_becomes_string():
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from filedge.parser import ParquetParser
    buf = io.BytesIO()
    pq.write_table(pa.table({"tags": pa.array([["a", "b"], ["c"]])}), buf)
    buf.seek(0)
    rows = list(ParquetParser().parse(buf))
    assert isinstance(rows[0]["tags"], str)


def test_parquet_parser_null_values():
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from filedge.parser import ParquetParser
    buf = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2], "amount": pa.array([9.99, None])}), buf)
    buf.seek(0)
    rows = list(ParquetParser().parse(buf))
    assert rows[1]["amount"] is None


def test_parquet_parser_registered_in_get_parser():
    pytest.importorskip("pyarrow")
    from filedge.parser import get_parser, ParquetParser
    assert isinstance(get_parser("parquet"), ParquetParser)
