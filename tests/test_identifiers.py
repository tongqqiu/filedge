import pytest

from filedge.config import ColumnMapping, PipelineConfig
from filedge.identifiers import IdentifierError, quote_identifier, validate_pipeline_identifiers


def test_quote_identifier_quotes_valid_name():
    assert quote_identifier("order") == '"order"'


@pytest.mark.parametrize("name", ["123table", "has-dash", "has space", "schema.table", ""])
def test_quote_identifier_rejects_invalid_name(name):
    with pytest.raises(IdentifierError):
        quote_identifier(name)


def test_validate_pipeline_identifiers_rejects_bad_table_name():
    config = PipelineConfig(
        format="csv",
        dest_table="bad-table",
        columns=[ColumnMapping(source="name", dest="name", type="string")],
    )

    with pytest.raises(IdentifierError, match="destination table"):
        validate_pipeline_identifiers(config)


def test_validate_pipeline_identifiers_rejects_bad_column_name():
    config = PipelineConfig(
        format="csv",
        dest_table="items",
        columns=[ColumnMapping(source="bad column", dest="bad column", type="string")],
    )

    with pytest.raises(IdentifierError, match="destination column"):
        validate_pipeline_identifiers(config)
