"""The Sources Config loader validates one named API Source into a FetchPlan and
rejects a malformed config rather than tolerate it. No secret is read from the
file — credentials are named by environment variable.
"""

import pytest

from filedge.fetch.errors import SourcesConfigError
from filedge.fetch.sources_config import company_concept_url, load_sources

_VALID = """\
version: 1
sources:
  - name: github-commits
    type: github
    url: https://api.github.com/repos/o/r/commits
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      param: since
      field: commit.committer.date
    credential_env: GITHUB_TOKEN
    query:
      sha: main
    page_size: 50
    gzip: true
"""


def _write(tmp_path, text):
    path = tmp_path / "sources.yaml"
    path.write_text(text)
    return str(path)


def test_loads_a_valid_source_into_a_fetch_plan(tmp_path):
    plan = load_sources(_write(tmp_path, _VALID), "github-commits")

    assert plan.source_name == "github-commits"
    assert plan.source_type == "github"
    assert plan.url == "https://api.github.com/repos/o/r/commits"
    assert plan.cursor_param == "since"
    assert plan.cursor_field == "commit.committer.date"
    assert plan.query == {"sha": "main"}
    assert plan.page_size == 50
    assert plan.gzip is True
    assert plan.credential_env == "GITHUB_TOKEN"
    assert plan.headers == {}


def test_credential_resolves_from_the_environment_only(tmp_path, monkeypatch):
    plan = load_sources(_write(tmp_path, _VALID), "github-commits")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")

    assert plan.credential() == "ghp_secret"
    # The secret is not in the config text.
    assert "ghp_secret" not in _VALID


def test_missing_file_raises(tmp_path):
    with pytest.raises(SourcesConfigError, match="not found"):
        load_sources(str(tmp_path / "nope.yaml"), "x")


def test_unknown_source_lists_known_names(tmp_path):
    with pytest.raises(SourcesConfigError, match="github-commits"):
        load_sources(_write(tmp_path, _VALID), "missing")


def test_unsupported_version_rejected(tmp_path):
    text = _VALID.replace("version: 1", "version: 2")
    with pytest.raises(SourcesConfigError, match="version"):
        load_sources(_write(tmp_path, text), "github-commits")


def test_missing_required_field_rejected(tmp_path):
    text = _VALID.replace("    url: https://api.github.com/repos/o/r/commits\n", "")
    with pytest.raises(SourcesConfigError, match="url"):
        load_sources(_write(tmp_path, text), "github-commits")


def test_cursor_without_param_or_field_rejected(tmp_path):
    text = _VALID.replace("      param: since\n", "")
    with pytest.raises(SourcesConfigError, match="cursor"):
        load_sources(_write(tmp_path, text), "github-commits")


def test_empty_sources_list_rejected(tmp_path):
    with pytest.raises(SourcesConfigError, match="non-empty"):
        load_sources(_write(tmp_path, "version: 1\nsources: []\n"), "x")


def test_non_mapping_document_rejected(tmp_path):
    with pytest.raises(SourcesConfigError, match="must be a mapping"):
        load_sources(_write(tmp_path, "- a\n- b\n"), "x")


def test_duplicate_source_name_rejected(tmp_path):
    text = _VALID + """\
  - name: github-commits
    type: github
    url: https://api.github.com/repos/o/r/commits
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      param: since
      field: id
"""
    with pytest.raises(SourcesConfigError, match="Duplicate"):
        load_sources(_write(tmp_path, text), "github-commits")


def test_non_mapping_query_rejected(tmp_path):
    text = _VALID.replace("    query:\n      sha: main\n", "    query: not-a-mapping\n")
    with pytest.raises(SourcesConfigError, match="query"):
        load_sources(_write(tmp_path, text), "github-commits")


def test_loads_static_headers(tmp_path):
    text = _VALID.replace(
        "    query:\n      sha: main\n",
        "    headers:\n"
        "      User-Agent: Filedge Test contact@example.com\n"
        "      X-Source: filedge\n"
        "    query:\n"
        "      sha: main\n",
    )

    plan = load_sources(_write(tmp_path, text), "github-commits")

    assert plan.headers == {
        "User-Agent": "Filedge Test contact@example.com",
        "X-Source": "filedge",
    }


def test_non_mapping_headers_rejected(tmp_path):
    text = _VALID.replace("    query:\n      sha: main\n", "    headers: nope\n")
    with pytest.raises(SourcesConfigError, match="headers"):
        load_sources(_write(tmp_path, text), "github-commits")


def test_edgar_source_parses_with_company_concept_url_and_defaults(tmp_path):
    text = """\
version: 1
sources:
  - name: apple-revenues
    type: edgar
    cik: 320193
    concept: Revenues
    unit: USD
    user_agent: Filedge Example contact@example.com
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      field: filed
"""

    plan = load_sources(_write(tmp_path, text), "apple-revenues")

    assert plan.source_type == "edgar"
    assert plan.cik == "0000320193"
    assert plan.taxonomy == "us-gaap"
    assert plan.concept == "Revenues"
    assert plan.unit == "USD"
    assert plan.cursor_field == "filed"
    assert plan.cursor_param == "filed"
    assert plan.cursor_mode == "client"
    assert plan.record_path == "units.USD"
    assert plan.headers["User-Agent"] == "Filedge Example contact@example.com"
    assert plan.url == (
        "https://data.sec.gov/api/xbrl/companyconcept/"
        "CIK0000320193/us-gaap/Revenues.json"
    )


def test_edgar_source_allows_taxonomy_override(tmp_path):
    text = """\
version: 1
sources:
  - name: apple-assets
    type: edgar
    cik: "0000320193"
    taxonomy: dei
    concept: EntityCommonStockSharesOutstanding
    unit: shares
    user_agent: Filedge Example contact@example.com
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      field: filed
"""

    plan = load_sources(_write(tmp_path, text), "apple-assets")

    assert plan.taxonomy == "dei"
    assert plan.record_path == "units.shares"
    assert plan.url.endswith(
        "/CIK0000320193/dei/EntityCommonStockSharesOutstanding.json"
    )


def test_company_concept_url_zero_pads_cik():
    assert company_concept_url(
        cik="320193", taxonomy="us-gaap", concept="Revenues"
    ) == (
        "https://data.sec.gov/api/xbrl/companyconcept/"
        "CIK0000320193/us-gaap/Revenues.json"
    )


def test_edgar_user_agent_is_required(tmp_path):
    text = """\
version: 1
sources:
  - name: apple-revenues
    type: edgar
    cik: 320193
    concept: Revenues
    unit: USD
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      field: filed
"""
    with pytest.raises(SourcesConfigError, match="user_agent"):
        load_sources(_write(tmp_path, text), "apple-revenues")


def test_edgar_rejects_malformed_cik(tmp_path):
    text = """\
version: 1
sources:
  - name: apple-revenues
    type: edgar
    cik: apple
    concept: Revenues
    unit: USD
    user_agent: Filedge Example contact@example.com
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
    cursor:
      field: filed
"""
    with pytest.raises(SourcesConfigError, match="cik"):
        load_sources(_write(tmp_path, text), "apple-revenues")


# --- Stripe source -------------------------------------------------------------

_STRIPE = """\
version: 1
sources:
  - name: stripe-charges
    type: stripe
    resource: charges
    credential_env: STRIPE_API_KEY
    staging_dir: ./staging
    watched_directory: ./landing
    state_dir: ./state
"""


def test_loads_a_stripe_source_with_sensible_defaults(tmp_path):
    plan = load_sources(_write(tmp_path, _STRIPE), "stripe-charges")

    assert plan.source_type == "stripe"
    assert plan.cursor_mode == "stripe"
    assert plan.resource == "charges"
    assert plan.url == "https://api.stripe.com/v1/charges"
    assert plan.record_path == "data"
    assert plan.cursor_field == "created"
    assert plan.cursor_param == "created[gt]"
    assert plan.credential_env == "STRIPE_API_KEY"


def test_stripe_api_base_override_points_at_a_mock(tmp_path):
    text = _STRIPE.replace(
        "    credential_env: STRIPE_API_KEY\n",
        "    credential_env: STRIPE_API_KEY\n    api_base: http://localhost:12111\n",
    )
    plan = load_sources(_write(tmp_path, text), "stripe-charges")
    assert plan.url == "http://localhost:12111/v1/charges"


def test_stripe_requires_resource_and_credential_env(tmp_path):
    no_resource = _STRIPE.replace("    resource: charges\n", "")
    with pytest.raises(SourcesConfigError, match="resource"):
        load_sources(_write(tmp_path, no_resource), "stripe-charges")

    no_cred = _STRIPE.replace("    credential_env: STRIPE_API_KEY\n", "")
    with pytest.raises(SourcesConfigError, match="credential_env"):
        load_sources(_write(tmp_path, no_cred), "stripe-charges")


def test_stripe_version_header_is_set_when_given(tmp_path):
    text = _STRIPE + "    stripe_version: '2024-06-20'\n"
    plan = load_sources(_write(tmp_path, text), "stripe-charges")
    assert plan.headers.get("Stripe-Version") == "2024-06-20"


def test_stripe_cursor_must_be_a_mapping(tmp_path):
    text = _STRIPE + "    cursor: not-a-mapping\n"
    with pytest.raises(SourcesConfigError, match="cursor"):
        load_sources(_write(tmp_path, text), "stripe-charges")
