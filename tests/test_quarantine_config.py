"""The opt-in `quarantine:` block parses into a QuarantineConfig and is rejected
when misconfigured. Absent or disabled → None (Strict Mode, the default).
"""

import pytest

from filedge.config import QuarantineConfig, config_from_dict

_BASE = {
    "format": "csv",
    "dest_table": "orders",
    "columns": [{"source": "id", "dest": "id", "type": "integer"}],
}


def _config(quarantine=None):
    data = dict(_BASE)
    if quarantine is not None:
        data["quarantine"] = quarantine
    return config_from_dict(data)


def test_absent_block_means_quarantine_disabled():
    assert _config().quarantine is None


def test_disabled_block_means_quarantine_disabled():
    assert _config({"enabled": False, "dir": "./q", "max_invalid_rows": 5}).quarantine is None


def test_enabled_block_parses_into_quarantine_config():
    q = _config({
        "enabled": True, "dir": "./quarantine",
        "max_invalid_fraction": 0.05, "max_invalid_rows": 100,
    }).quarantine

    assert isinstance(q, QuarantineConfig)
    assert q.dir == "./quarantine"
    assert q.max_invalid_fraction == 0.05
    assert q.max_invalid_rows == 100


def test_enabled_requires_a_dir():
    with pytest.raises(ValueError, match="dir"):
        _config({"enabled": True, "max_invalid_rows": 5})


def test_enabled_requires_a_threshold():
    with pytest.raises(ValueError, match="threshold"):
        _config({"enabled": True, "dir": "./q"})


def test_fraction_out_of_range_rejected():
    with pytest.raises(ValueError, match="between 0 and 1"):
        _config({"enabled": True, "dir": "./q", "max_invalid_fraction": 1.5})


def test_negative_row_limit_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        _config({"enabled": True, "dir": "./q", "max_invalid_rows": -1})


def test_non_mapping_block_rejected():
    with pytest.raises(ValueError, match="mapping"):
        _config("not-a-mapping")


def test_existing_config_without_quarantine_is_unchanged():
    config = _config()
    assert config.quarantine is None
    assert config.format == "csv"  # nothing else perturbed


# --- the threshold predicate ---

def test_over_threshold_by_row_count():
    q = QuarantineConfig(dir="./q", max_invalid_rows=10)
    assert q.is_over_threshold(invalid=11, total=1000) is True
    assert q.is_over_threshold(invalid=10, total=1000) is False  # exactly at the limit is OK


def test_over_threshold_by_fraction():
    q = QuarantineConfig(dir="./q", max_invalid_fraction=0.05)
    assert q.is_over_threshold(invalid=6, total=100) is True     # 6% > 5%
    assert q.is_over_threshold(invalid=5, total=100) is False    # exactly 5%


def test_over_threshold_if_either_limit_exceeded():
    q = QuarantineConfig(dir="./q", max_invalid_fraction=0.5, max_invalid_rows=10)
    # Fraction fine (1%) but row count exceeded.
    assert q.is_over_threshold(invalid=11, total=1100) is True
