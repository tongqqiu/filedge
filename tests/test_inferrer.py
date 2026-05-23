from filedge.inferrer import infer_schema


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


# --- boolean ---

def test_true_false_strings_inferred_as_boolean():
    result = infer_schema(rows({"b": "true"}, {"b": "false"}, {"b": "True"}))
    col = result[0]
    assert col.inferred_type == "boolean"
    assert col.confidence == "high"


def test_yes_no_strings_inferred_as_boolean():
    result = infer_schema(rows({"b": "yes"}, {"b": "no"}))
    assert result[0].inferred_type == "boolean"


def test_one_zero_alone_stays_integer_not_boolean():
    result = infer_schema(rows({"b": "1"}, {"b": "0"}, {"b": "1"}))
    assert result[0].inferred_type == "integer"


# --- date / timestamp ---

def test_iso_date_column():
    result = infer_schema(rows({"d": "2024-01-15"}, {"d": "2024-06-30"}))
    col = result[0]
    assert col.inferred_type == "date"
    assert col.confidence == "high"


def test_datetime_with_time_component_is_timestamp():
    result = infer_schema(rows({"ts": "2024-01-15T10:30:00"}, {"ts": "2024-06-30 08:00:00"}))
    assert result[0].inferred_type == "timestamp"


def test_mixed_date_formats_is_string_ambiguous_with_note():
    result = infer_schema(rows({"d": "2024-01-15"}, {"d": "01/15/2024"}))
    col = result[0]
    assert col.inferred_type == "string"
    assert col.confidence == "ambiguous"
    assert any("date format" in n for n in col.notes)


# --- nested objects / arrays (#22) ---

def test_nested_dict_value_typed_as_string_with_keys_note():
    result = infer_schema(rows(
        {"meta": {"currency": "USD", "region": "US"}},
        {"meta": {"currency": "EUR", "region": "EU"}},
    ))
    col = result[0]
    assert col.inferred_type == "string"
    assert col.confidence == "ambiguous"
    assert any("currency" in n and "region" in n for n in col.notes)


def test_array_value_typed_as_string_with_note():
    result = infer_schema(rows({"tags": ["a", "b"]}, {"tags": ["c"]}))
    col = result[0]
    assert col.inferred_type == "string"
    assert col.confidence == "ambiguous"
    assert any("array" in n for n in col.notes)


def test_mixed_scalar_and_dict_is_string_ambiguous():
    result = infer_schema(rows({"x": "hello"}, {"x": {"nested": 1}}))
    col = result[0]
    assert col.inferred_type == "string"
    assert col.confidence == "ambiguous"


# --- existing null tests (unchanged) ---

def test_empty_string_counts_as_null():
    result = infer_schema(rows({"n": "1"}, {"n": ""}, {"n": "3"}))
    col = result[0]
    assert col.null_count == 1
    assert col.inferred_type == "integer"
    assert col.confidence == "low"
