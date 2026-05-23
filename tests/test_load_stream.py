import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.load_stream import LoadStream, LoadStreamError, iter_transformed_rows


@pytest.fixture
def config():
    return PipelineConfig(
        format="csv",
        dest_table="items",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
            ColumnMapping(source="value", dest="value", type="integer", required=True),
        ],
    )


def test_iter_transformed_rows_yields_rows_and_counts(config, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("name,value\nalpha,1\nbeta,2\n")
    stream = LoadStream()

    rows = list(iter_transformed_rows(config, str(f), stream=stream))

    assert rows == [
        {"name": "alpha", "value": 1},
        {"name": "beta", "value": 2},
    ]
    assert stream.rows_loaded == 2


def test_iter_transformed_rows_reports_transform_error_with_row_number(config, tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("name,value\nalpha,1\nbeta,nope\n")
    stream = LoadStream()

    with pytest.raises(LoadStreamError) as exc:
        list(iter_transformed_rows(config, str(f), stream=stream))

    assert exc.value.row_number == 2
    assert "Row 2" in str(exc.value)
    assert "value" in str(exc.value)
    assert stream.rows_loaded == 1


def test_iter_transformed_rows_reports_parse_error_with_row_number(tmp_path):
    config = PipelineConfig(
        format="ndjson",
        dest_table="items",
        columns=[
            ColumnMapping(source="name", dest="name", type="string", required=True),
        ],
    )
    f = tmp_path / "bad.ndjson"
    f.write_text('{"name": "alpha"}\n{"name": \n')
    stream = LoadStream()

    with pytest.raises(LoadStreamError) as exc:
        list(iter_transformed_rows(config, str(f), stream=stream))

    assert exc.value.row_number == 2
    assert "Row 2: parse error" in str(exc.value)
    assert stream.rows_loaded == 1
