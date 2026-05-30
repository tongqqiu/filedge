"""The Sources Config loader validates one named API Source into a FetchPlan and
rejects a malformed config rather than tolerate it. No secret is read from the
file — credentials are named by environment variable.
"""

import pytest

from filedge.fetch.errors import SourcesConfigError
from filedge.fetch.sources_config import load_sources

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
