"""Label-override and region-pattern nits on the GT v2.6 mapper.

Purpose: lock the review fixes that do not need the raw GT workbook.
Use case: a missing or stale ``label_overrides.json`` must fail the run,
and the country patterns must be case-insensitive like the India rules.
Example: ``load_label_overrides`` on a path that does not exist raises
``LabelOverrideError`` instead of returning ``{}``.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

from mapping.map_gt import (
    _COUNTRY_PREP,
    _REGION_LIST_R7,
    LabelOverrideError,
    apply_label_overrides,
    extract_qualification,
    load_label_overrides,
    load_vocab,
)


def test_checked_in_overrides_cover_l1_and_l2() -> None:
    overrides = load_label_overrides()
    assert set(overrides) == {"GT-LBS-DIR-039", "GT-LBS-AC-073"}


def test_missing_overrides_file_raises(tmp_path) -> None:
    missing = tmp_path / "label_overrides.json"
    with pytest.raises(LabelOverrideError, match="required and was not found"):
        load_label_overrides(missing)


def test_matching_override_is_applied_and_does_not_warn() -> None:
    vocab = load_vocab()
    row = pd.Series(
        {
            "id": "GT-LBS-DIR-039",
            "intent": "sales_qualification",
            "sub_intent": "capability_fit",
            "primary_team": "Sales",
            "api": "directions",
        }
    )
    updated, issues = apply_label_overrides(row, load_label_overrides(), vocab)
    assert issues == []
    assert updated["intent"] == "capability_match"
    assert updated["sub_intent"] == "capability_matching"
    assert row["intent"] == "sales_qualification"


def test_stale_from_values_fail_instead_of_warning() -> None:
    vocab = load_vocab()
    row = pd.Series(
        {
            "id": "GT-LBS-DIR-039",
            "intent": "pricing_quote",
            "sub_intent": "pricing_quote",
            "primary_team": "Sales",
            "api": "directions",
        }
    )
    overrides = {
        "GT-LBS-DIR-039": {
            "from": {
                "intent": "sales_qualification",
                "sub_intent": "capability_fit",
                "primary_team": "Sales",
            },
            "to": {"intent": "capability_match", "sub_intent": "capability_matching"},
        }
    }
    with pytest.raises(LabelOverrideError, match="label_override_stale"):
        apply_label_overrides(row, overrides, vocab)
    assert row["intent"] == "pricing_quote"


def test_broken_override_fails_instead_of_warning() -> None:
    vocab = load_vocab()
    row = pd.Series(
        {
            "id": "GT-LBS-AC-073",
            "intent": "capability_match",
            "sub_intent": "capability_question",
            "primary_team": "Sales",
            "api": "autocomplete",
        }
    )
    overrides = {
        "GT-LBS-AC-073": {
            "from": {
                "intent": "capability_match",
                "sub_intent": "capability_question",
                "primary_team": "Sales",
            },
            "to": {"intent": "not_an_intent", "sub_intent": "accuracy_question"},
        }
    }
    with pytest.raises(LabelOverrideError, match="label_override_invalid"):
        apply_label_overrides(row, overrides, vocab)
    assert row["intent"] == "capability_match"


def test_country_patterns_match_india_rules_case_insensitively() -> None:
    assert _COUNTRY_PREP.flags & re.IGNORECASE
    assert _REGION_LIST_R7.flags & re.IGNORECASE
    vocab = load_vocab()
    assert extract_qualification("Routes for Bhutan today.", "directions", vocab)["region"] == "Bhutan"
    lowered = extract_qualification("Routes for bhutan today.", "directions", vocab)["region"]
    assert lowered is not None and lowered.casefold() == "bhutan"
    assert extract_qualification("Routes across India.", "directions", vocab)["region"] == "India"
    assert extract_qualification("Routes IN INDIA.", "directions", vocab)["region"] == "India"


def test_india_alternation_lists_across_once() -> None:
    import inspect

    from mapping.map_gt import extract_qualification as fn

    source = inspect.getsource(fn)
    assert "throughout|across" not in source
    assert "in|across|for|throughout" in source
