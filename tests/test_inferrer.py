from etl.inferrer import InferredColumn, infer_schema


def rows(*dicts):
    return iter(dicts)


# --- integer ---

def test_clean_integer_column():
    result = infer_schema(rows({"n": "1"}, {"n": "2"}, {"n": "3"}))
    col = result[0]
    assert col.name == "n"
    assert col.inferred_type == "integer"
    assert col.confidence == "high"
    assert col.null_count == 0
    assert col.total_seen == 3


def test_clean_float_column():
    result = infer_schema(rows({"x": "1.5"}, {"x": "2.0"}, {"x": "3.14"}))
    col = result[0]
    assert col.inferred_type == "float"
    assert col.confidence == "high"
    assert col.null_count == 0


def test_mixed_column_is_string_ambiguous():
    result = infer_schema(rows({"v": "1"}, {"v": "hello"}, {"v": "2"}))
    col = result[0]
    assert col.inferred_type == "string"
    assert col.confidence == "ambiguous"


def test_integer_with_nulls_is_low_confidence():
    result = infer_schema(rows({"n": "1"}, {"n": None}, {"n": ""}, {"n": "3"}))
    col = result[0]
    assert col.inferred_type == "integer"
    assert col.confidence == "low"
    assert col.null_count == 2


def test_all_null_column_is_string_ambiguous():
    result = infer_schema(rows({"n": None}, {"n": ""}, {"n": None}))
    col = result[0]
    assert col.inferred_type == "string"
    assert col.confidence == "ambiguous"
    assert col.null_count == 3


def test_multiple_columns_returned():
    result = infer_schema(rows({"a": "1", "b": "hello"}, {"a": "2", "b": "world"}))
    assert len(result) == 2
    names = {c.name for c in result}
    assert names == {"a", "b"}


def test_sample_rows_stops_iteration_early():
    consumed = []

    def gen():
        for i in range(100):
            consumed.append(i)
            yield {"n": str(i)}

    infer_schema(gen(), sample_rows=10)
    assert len(consumed) == 10


def test_total_seen_reflects_rows_consumed():
    result = infer_schema(rows({"n": "1"}, {"n": "2"}, {"n": "3"}), sample_rows=2)
    assert result[0].total_seen == 2


def test_empty_string_counts_as_null():
    result = infer_schema(rows({"n": "1"}, {"n": ""}, {"n": "3"}))
    col = result[0]
    assert col.null_count == 1
    assert col.inferred_type == "integer"
    assert col.confidence == "low"
