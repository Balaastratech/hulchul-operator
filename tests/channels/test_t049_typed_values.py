"""which control answers a field, and the typed value the graph expects."""
from __future__ import annotations

import pytest

from src.operator.channels.questions import (
    InvalidAnswer,
    coerce_value,
    control_kind,
    group_siblings,
    option_label,
)

Q = "Preferred work mode"


@pytest.mark.parametrize("key,options,expected", [
    ("Yes|radio|Eligible to work? *|0", None, "radio"),
    ("Remote|checkbox|Preferred work mode|0", None, "checkbox"),
    ("Terms *|checkbox||0", None, "checkbox"),
    ("Visa status|select||0", ["Citizen", "Work visa"], "select"),
    ("Visa status|select||0", [], "text"),       # a dropdown needs its options
    ("Visa status|select||0", None, "text"),
    ("Why us|textarea||0", None, "text"),
    ("Phone|tel||0", None, "text"),
    ("email", None, "text"),                     # no four-part key: text
    ("python", ["a"], "text"),
])
def test_control_kind(key, options, expected):
    assert control_kind(key, options) == expected


@pytest.mark.parametrize("key,value,expected", [
    ("Terms|checkbox||0", "true", True),
    ("Terms|checkbox||0", "on", True),
    ("Terms|checkbox||0", "false", False),
    ("Terms|checkbox||0", "", False),
    ("Terms|checkbox||0", True, True),
    ("Terms|checkbox||0", False, False),
    ("Yes|radio|Q|0", "true", True),
    ("Yes|radio|Q|0", True, True),
    ("Phone|text||0", "true", "true"),           # text stays text
    ("Visa|select||0", "Work visa", "Work visa"),
    ("python", True, True),                      # keys without a type pass through unchanged
    ("python", "30 days", "30 days"),
])
def test_coerce_value(key, value, expected):
    result = coerce_value(key, value)
    assert result == expected and type(result) is type(expected)


@pytest.mark.parametrize("key,value", [
    ("Yes|radio|Q|0", False),                    # a radio option can only be selected
    ("Yes|radio|Q|0", "false"),
    ("Terms|checkbox||0", "maybe"),
    ("Terms|checkbox||0", 1),
    ("Terms|checkbox||0", None),
    ("Terms|checkbox||0", ["true"]),
])
def test_coerce_value_rejects_what_the_graph_would_refuse(key, value):
    with pytest.raises(InvalidAnswer):
        coerce_value(key, value)


def test_group_siblings_and_option_labels():
    keys = [f"{o}|checkbox|{Q}|0" for o in ("Remote", "Hybrid", "Onsite")] + ["Terms|checkbox||0"]
    assert group_siblings(keys[1], keys) == keys[:3]
    assert group_siblings(keys[3], keys) == [keys[3]]           # no group: alone
    assert group_siblings("new|checkbox|Other|0", keys) == ["new|checkbox|Other|0"]
    assert [option_label(k) for k in keys[:3]] == ["Remote", "Hybrid", "Onsite"]
