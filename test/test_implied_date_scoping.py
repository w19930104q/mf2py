"""Offline regression tests for implied-date (default date) scoping.

The implied date of a dt-* property must only be inherited from the nearest
preceding valid date authored inside the *same* microformat root (in document
order). Nested roots start from an empty date, the parent's date is restored
when a nested root finishes, and sibling roots never influence each other.

See:
https://microformats.org/wiki/value-class-pattern#microformats2_parsers_implied_date
"""

from bs4 import BeautifulSoup

from mf2py import Parser, parse


def parse_html(html, **kwargs):
    return parse(doc=html, html_parser="html5lib", **kwargs)


def test_child_date_does_not_leak_to_parent():
    """Parent date A, child date B: a parent time-only dt still uses A."""
    html = """
    <div class="h-event" id="parent">
      <span class="dt-start">2014-05-21 18:30</span>
      <div class="h-event" id="child">
        <span class="dt-start">2099-01-01 09:00</span>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    parent = result["items"][0]

    assert parent["properties"]["start"] == ["2014-05-21 18:30"]
    # the parent's end must keep the parent's own date, not the child's
    assert parent["properties"]["end"] == ["2014-05-21 19:30"]

    child = parent["children"][0]
    assert child["id"] == "child"
    assert child["properties"]["start"] == ["2099-01-01 09:00"]


def test_nested_root_restores_parent_date_after_exit():
    """A child root with no date must not erase the parent's implied date."""
    html = """
    <div class="h-event">
      <span class="dt-start">2014-05-21 18:30</span>
      <div class="h-card">
        <p class="p-name">Some person</p>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    parent = result["items"][0]
    assert parent["properties"]["start"] == ["2014-05-21 18:30"]
    assert parent["properties"]["end"] == ["2014-05-21 19:30"]


def test_sibling_roots_do_not_share_dates():
    """Dates of one sibling root must not reach another sibling or the parent."""
    html = """
    <div class="h-event" id="parent">
      <div class="h-event" id="sibling-a">
        <span class="dt-start">2099-01-01 09:00</span>
        <span class="dt-end">10:00</span>
      </div>
      <div class="h-event" id="sibling-b">
        <span class="dt-start">2088-02-02 08:00</span>
        <span class="dt-end">11:00</span>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    parent = result["items"][0]

    sibling_a, sibling_b = parent["children"]
    assert sibling_a["properties"]["start"] == ["2099-01-01 09:00"]
    assert sibling_a["properties"]["end"] == ["2099-01-01 10:00"]
    assert sibling_b["properties"]["start"] == ["2088-02-02 08:00"]
    assert sibling_b["properties"]["end"] == ["2088-02-02 11:00"]

    # the parent has no date of its own, so a time-only property must remain
    # a time rather than inheriting a sibling's date
    assert parent["properties"]["end"] == ["19:30"]


def test_deeply_nested_roots_have_independent_date_scopes():
    html = """
    <div class="h-event" id="root">
      <span class="dt-start">2014-05-21 18:30</span>
      <div class="h-event" id="child">
        <span class="dt-start">2099-01-01 09:00</span>
        <div class="h-event" id="grandchild">
          <span class="dt-start">1999-12-31 23:00</span>
        </div>
        <span class="dt-end">10:00</span>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    root = result["items"][0]
    child = root["children"][0]
    grandchild = child["children"][0]

    assert root["properties"]["end"] == ["2014-05-21 19:30"]
    assert child["properties"]["end"] == ["2099-01-01 10:00"]
    assert grandchild["properties"]["start"] == ["1999-12-31 23:00"]


def test_root_that_is_also_a_dt_property():
    """The dt value of the root element itself resolves in the parent scope,
    while properties nested inside that root start from an empty date."""
    html = """
    <div class="h-event" id="outer">
      <span class="dt-start">2014-05-21 18:30</span>
      <time class="h-event dt-updated" datetime="21:00">
        <span class="dt-end">22:00</span>
      </time>
    </div>
    """
    result = parse_html(html)
    outer = result["items"][0]
    assert outer["properties"]["start"] == ["2014-05-21 18:30"]

    nested = outer["properties"]["updated"][0]
    assert nested["type"] == ["h-event"]
    # the element's own dt value uses the enclosing root's implied date
    assert nested["value"] == "2014-05-21 21:00"
    # the nested root's internal property starts from an empty date
    assert nested["properties"]["end"] == ["22:00"]


def test_plain_date_only_property():
    """A date without time establishes the implied date for later times."""
    html = """
    <div class="h-event">
      <time class="dt-start" datetime="2014-05-21">the day</time>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    props = result["items"][0]["properties"]
    assert props["start"] == ["2014-05-21"]
    assert props["end"] == ["2014-05-21 19:30"]


def test_time_only_property_without_date_stays_a_time():
    """No machine/current date may be injected into a dateless time."""
    html = """
    <div class="h-event">
      <span class="dt-start">18:30</span>
    </div>
    """
    result = parse_html(html)
    assert result["items"][0]["properties"]["start"] == ["18:30"]


def test_empty_vcp_pieces_do_not_crash_or_produce_a_date():
    html = """
    <div class="h-event">
      <span class="dt-start">
        <span class="value"></span>
        <span class="value-title"></span>
      </span>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    props = result["items"][0]["properties"]
    # empty pieces yield no dt value and no implied date ...
    assert props.get("start", []) == []
    # ... so the following time stays a bare time
    assert props["end"] == ["19:30"]


def test_timezone_only_vcp_pieces_do_not_crash_or_change_state():
    html = """
    <div class="h-event">
      <span class="dt-start">
        <span class="value">2014-06-01</span>
      </span>
      <span class="dt-foo">
        <span class="value">-06:00</span>
      </span>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    props = result["items"][0]["properties"]
    # a timezone-only property produces nothing ...
    assert props.get("foo", []) == []
    # ... and must not reset the implied date established by dt-start
    assert props["end"] == ["2014-06-01 19:30"]


def test_value_title_reads_each_pieces_own_title():
    """A value-title contributes its own title, never the parent's title."""
    html = """
    <div class="h-event">
      <span class="dt-start" title="PARENT-TITLE-MUST-NOT-BE-USED">
        <span class="value-title" title="2014-06-01"></span>
        <span class="value-title" title="12:30"></span>
        <span class="value-title" title="-06:00"></span>
      </span>
    </div>
    """
    result = parse_html(html)
    assert result["items"][0]["properties"]["start"] == ["2014-06-01 12:30-0600"]


def test_vcp_datetime_pieces_follow_same_implied_date_rules():
    """VCP-assembled times inherit a date only from their own root."""
    html = """
    <div class="h-event" id="parent">
      <span class="dt-start">
        <span class="value">2014-05-21</span>
        <span class="value">18:30</span>
      </span>
      <div class="h-event" id="child">
        <span class="dt-start">
          <span class="value">2099-01-01</span>
          <span class="value">09:00</span>
        </span>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    parent = result["items"][0]
    assert parent["properties"]["start"] == ["2014-05-21 18:30"]
    assert parent["properties"]["end"] == ["2014-05-21 19:30"]
    assert parent["children"][0]["properties"]["start"] == ["2099-01-01 09:00"]


def test_twelve_hour_times_are_normalized_with_implied_date():
    html = """
    <div class="h-event">
      <span class="dt-start">
        <span class="value">2014-06-01</span>
        <span class="value">3:28pm</span>
      </span>
      <span class="dt-end">12:15a.m.</span>
    </div>
    """
    result = parse_html(html)
    props = result["items"][0]["properties"]
    assert props["start"] == ["2014-06-01 15:28"]
    assert props["end"] == ["2014-06-01 00:15"]


def test_mf1_backcompat_keeps_date_scopes_separate():
    """Legacy vevent markup must obey the same nested-date scoping rules."""
    html = """
    <div class="vevent" id="parent">
      <span class="dtstart">2014-05-21 18:30</span>
      <div class="vevent" id="child">
        <span class="dtstart">2099-01-01 09:00</span>
      </div>
      <span class="dtend">19:30</span>
    </div>
    """
    result = parse_html(html)
    parent = result["items"][0]
    assert "h-event" in parent["type"]
    assert parent["properties"]["start"] == ["2014-05-21 18:30"]
    assert parent["properties"]["end"] == ["2014-05-21 19:30"]
    assert parent["children"][0]["properties"]["start"] == ["2099-01-01 09:00"]


def test_same_element_multiple_dt_properties_share_value():
    html = """
    <div class="h-entry">
      <time class="dt-published dt-updated" datetime="2014-05-21 18:30">x</time>
      <span class="dt-end">19:30</span>
    </div>
    """
    props = parse_html(html)["items"][0]["properties"]
    assert props["published"] == ["2014-05-21 18:30"]
    assert props["updated"] == ["2014-05-21 18:30"]
    assert props["end"] == ["2014-05-21 19:30"]


def test_repeated_parsing_is_deterministic():
    """Parser state must not leak between documents or repeated parses."""
    html = """
    <div class="h-event">
      <span class="dt-start">2014-05-21 18:30</span>
      <div class="h-event">
        <span class="dt-start">2099-01-01 09:00</span>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    first = parse_html(html)
    for _ in range(3):
        assert parse_html(html) == first


def test_parsing_does_not_mutate_input_dom():
    html = """
    <div class="h-event" id="parent">
      <span class="dt-start">2014-05-21 18:30</span>
      <div class="h-event" id="child">
        <span class="dt-start">2099-01-01 09:00</span>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    soup = BeautifulSoup(html, features="html5lib")
    before = str(soup)
    Parser(doc=soup)
    after = str(soup)
    assert before == after


def test_full_structure_of_nested_event():
    """Assert the complete parsed structure, not just the date values."""
    html = """
    <div class="h-event" id="parent">
      <h1 class="p-name">Parent Event</h1>
      <span class="dt-start">2014-05-21 18:30</span>
      <div class="h-event" id="child">
        <h1 class="p-name">Child Event</h1>
        <span class="dt-start">2099-01-01 09:00</span>
      </div>
      <span class="dt-end">19:30</span>
    </div>
    """
    result = parse_html(html)
    assert result["items"] == [
        {
            "type": ["h-event"],
            "id": "parent",
            "properties": {
                "name": ["Parent Event"],
                "start": ["2014-05-21 18:30"],
                "end": ["2014-05-21 19:30"],
            },
            "children": [
                {
                    "type": ["h-event"],
                    "id": "child",
                    "properties": {
                        "name": ["Child Event"],
                        "start": ["2099-01-01 09:00"],
                    },
                }
            ],
        }
    ]
