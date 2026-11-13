"""Offline regression tests for the include-pattern (include=True) feature."""

import pytest
from bs4 import BeautifulSoup

import mf2py
from mf2py import IncludeError, IncludeLimitExceeded, Parser, parse


def parse_doc(doc, **kwargs):
    kwargs.setdefault("html_parser", "html5lib")
    return Parser(doc=doc, **kwargs).to_dict()


SHARED_VENUE = """
<html><body>
<div id="venue" class="h-card">
  <span class="p-name">The Grand Hall</span>
  <span class="p-street-address"><span class="value">1 Main St</span></span>
</div>
<div class="h-event">
  <span class="p-name">Concert A</span>
  <object class="include" data="#venue"></object>
</div>
<div class="h-event">
  <span class="p-name">Concert B</span>
  <a class="include" href="#venue"></a>
</div>
</body></html>
"""


def test_default_off_unchanged():
    """include mode is off by default: references are not expanded and no
    diagnostics are reported"""
    result = parse_doc(SHARED_VENUE)
    assert "include-diagnostics" not in result["debug"]
    events = [i for i in result["items"] if "h-event" in i["type"]]
    assert len(events) == 2
    for event in events:
        assert "children" not in event
        assert event["properties"]["name"][0] in ("Concert A", "Concert B")


def test_shared_venue_mf2():
    """the headline use case: several records reference shared venue data;
    a.include and object.include both expand at the reference position"""
    result = parse_doc(SHARED_VENUE, include=True)
    assert result["debug"]["include-diagnostics"] == []

    events = [i for i in result["items"] if "h-event" in i["type"]]
    assert len(events) == 2
    for event in events:
        # venue appears as a nested child of each event
        assert len(event["children"]) == 1
        venue = event["children"][0]
        assert venue["type"] == ["h-card"]
        assert venue["id"] == "venue"
        assert venue["properties"]["name"] == ["The Grand Hall"]
        # value-class-pattern applies inside expanded content
        assert venue["properties"]["street-address"] == ["1 Main St"]
        # nested h-* scoping: venue properties do not leak into the event
        assert "street-address" not in event["properties"]

    # the venue itself is still a top-level item as well
    assert any("h-card" in i["type"] for i in result["items"])


def test_forward_and_backward_reference():
    """references work no matter whether the target comes before or after"""
    doc = """
    <div class="h-entry"><a class="include" href="#later"></a></div>
    <span id="later" class="p-category">forward</span>
    <span id="earlier" class="p-category">backward</span>
    <div class="h-entry"><a class="include" href="#earlier"></a></div>
    """
    result = parse_doc(doc, include=True)
    entries = [i for i in result["items"] if "h-entry" in i["type"]]
    assert entries[0]["properties"]["category"] == ["forward"]
    assert entries[1]["properties"]["category"] == ["backward"]


def test_multi_level_and_duplicate_values():
    """multi-level references expand recursively; repeated references keep
    duplicate values"""
    doc = """
    <span id="c" class="p-tag">deep</span>
    <div id="b"><object class="include" data="#c"></object></div>
    <div class="h-entry">
      <a class="include" href="#b"></a>
      <a class="include" href="#c"></a>
      <a class="include" href="#c"></a>
    </div>
    """
    result = parse_doc(doc, include=True)
    entry = [i for i in result["items"] if "h-entry" in i["type"]][0]
    # one "deep" via #b and two direct ones via #c: duplicates preserved
    assert entry["properties"]["tag"] == ["deep", "deep", "deep"]


def test_mf1_backcompat():
    """includes participate in microformats1 backcompat extraction"""
    doc = """
    <div id="vc" class="vcard"><span class="fn">Hall</span></div>
    <div class="vevent">
      <span class="summary">Gig</span>
      <object class="include" data="#vc"></object>
    </div>
    """
    result = parse_doc(doc, include=True)
    event = [i for i in result["items"] if "h-event" in i["type"]][0]
    assert event["properties"]["name"] == ["Gig"]
    assert event["children"][0]["type"] == ["h-card"]
    assert event["children"][0]["properties"]["name"] == ["Hall"]


def test_include_element_own_classes_ignored():
    """class names on the reference element itself are ignored"""
    doc = """
    <span id="n" class="p-name">Real Name</span>
    <div class="h-entry">
      <a class="include p-name u-url" href="#n">ignored text</a>
    </div>
    """
    result = parse_doc(doc, include=True)
    entry = [i for i in result["items"] if "h-entry" in i["type"]][0]
    assert entry["properties"]["name"] == ["Real Name"]
    assert "url" not in entry["properties"]


def test_implied_properties_in_expanded_content():
    """implied name/photo rules apply to expanded content"""
    doc = """
    <div id="v" class="h-card"><img src="hall.png" alt="The Hall"></div>
    <div class="h-event"><a class="include" href="#v"></a></div>
    """
    result = parse_doc(doc, url="http://example.com/events", include=True)
    event = [i for i in result["items"] if "h-event" in i["type"]][0]
    card = event["children"][0]
    assert card["properties"]["name"] == ["The Hall"]
    assert card["properties"]["photo"] == [
        {"value": "http://example.com/hall.png", "alt": "The Hall"}
    ]


def test_self_reference():
    doc = """
    <div class="h-entry"><p class="p-name">Keep me</p></div>
    <a id="s1" class="include" href="#s1"></a>
    """
    result = parse_doc(doc, include=True)
    diags = result["debug"]["include-diagnostics"]
    assert [d["reason"] for d in diags] == ["self-reference"]
    assert diags[0]["target"] == "s1"
    # other records are still parsed
    assert len(result["items"]) == 1
    assert result["items"][0]["properties"]["name"] == ["Keep me"]


def test_ancestor_reference():
    doc = """
    <div id="anc">
      <span class="p-name">outer</span>
      <a class="include" href="#anc"></a>
    </div>
    """
    result = parse_doc(doc, include=True)
    diags = result["debug"]["include-diagnostics"]
    assert [d["reason"] for d in diags] == ["ancestor-reference"]
    assert diags[0]["target"] == "anc"


def test_indirect_cycle():
    """a includes b, b includes a: expansion terminates and reports"""
    doc = """
    <div id="a"><span class="p-name">A</span><object class="include" data="#b"></object></div>
    <div id="b"><span class="p-name">B</span><a class="include" href="#a"></a></div>
    """
    result = parse_doc(doc, include=True)
    diags = result["debug"]["include-diagnostics"]
    assert len(diags) == 2
    assert {d["reason"] for d in diags} <= {"ancestor-reference", "circular-reference"}
    assert {d["target"] for d in diags} == {"a", "b"}


def test_indirect_cycle_circular_reason():
    """a loop that closes purely through expanded copies is reported as
    circular-reference"""
    doc = """
    <div id="a"><object class="include" data="#b"></object></div>
    <div id="b"><object class="include" data="#c"></object></div>
    <div id="c"><object class="include" data="#b"></object></div>
    """
    result = parse_doc(doc, include=True)
    reasons = [d["reason"] for d in result["debug"]["include-diagnostics"]]
    assert "circular-reference" in reasons


def test_missing_id():
    doc = """
    <div class="h-entry"><p class="p-name">Still here</p></div>
    <a class="include" href="#nope"></a>
    """
    result = parse_doc(doc, include=True)
    diags = result["debug"]["include-diagnostics"]
    assert [d["reason"] for d in diags] == ["missing-id"]
    assert diags[0]["target"] == "nope"
    assert result["items"][0]["properties"]["name"] == ["Still here"]


def test_duplicate_id():
    doc = """
    <span id="dup">one</span><span id="dup">two</span>
    <a class="include" href="#dup"></a>
    """
    result = parse_doc(doc, include=True)
    diags = result["debug"]["include-diagnostics"]
    assert [d["reason"] for d in diags] == ["duplicate-id"]
    assert diags[0]["target"] == "dup"


def test_external_reference_not_fetched():
    """references to other pages are never fetched and not expanded"""
    doc = """
    <div class="h-entry">
      <p class="p-name">Entry</p>
      <a class="include" href="https://other.example/page#frag"></a>
      <object class="include" data="other.html#frag"></object>
    </div>
    """
    result = parse_doc(doc, url="http://example.com/", include=True)
    diags = result["debug"]["include-diagnostics"]
    assert [d["reason"] for d in diags] == ["external-reference"] * 2
    assert result["items"][0]["properties"]["name"] == ["Entry"]


def test_missing_reference():
    doc = """
    <a class="include"></a>
    <object class="include"></object>
    <a class="include" href="#"></a>
    """
    result = parse_doc(doc, include=True)
    diags = result["debug"]["include-diagnostics"]
    assert [d["reason"] for d in diags] == ["missing-reference"] * 3


def test_diagnostics_are_locatable():
    doc = '<div class="h-entry">\n  <p class="p-name">x</p>\n  <a class="include" href="#gone"></a>\n</div>'
    result = parse_doc(doc, include=True)
    (diag,) = result["debug"]["include-diagnostics"]
    assert diag["reason"] == "missing-id"
    assert diag["reference"] == "#gone"
    assert diag["target"] == "gone"
    assert '<a class="include" href="#gone">' in diag["element"]
    assert diag["sourceline"] == 3
    assert isinstance(diag["sourcepos"], int)


def test_node_limit_fails_explicitly():
    """exceeding the expansion node cap raises instead of delivering
    truncated data"""
    doc = """
    <div id="v"><span class="p-name">V</span><span class="p-geo">G</span></div>
    <div class="h-entry"><a class="include" href="#v"></a></div>
    <div class="h-entry"><a class="include" href="#v"></a></div>
    """
    # one expansion fits, two exceed the cap
    with pytest.raises(IncludeLimitExceeded):
        parse_doc(doc, include=True, include_max_nodes=6)
    # also raised through the convenience API
    with pytest.raises(IncludeError):
        parse(doc, include=True, include_max_nodes=6)
    # generous limit parses fine
    result = parse_doc(doc, include=True, include_max_nodes=100)
    assert len(result["items"]) == 2


def test_node_limit_none_disables_cap():
    doc = """
    <div id="v"><span class="p-name">V</span></div>
    <div class="h-entry"><a class="include" href="#v"></a></div>
    """
    result = parse_doc(doc, include=True, include_max_nodes=None)
    assert result["items"][0]["properties"]["name"] == ["V"]


def test_e_content_outputs_unexpanded_html():
    """e-* properties keep the originally authored HTML (with the include
    element), subject to the usual URL resolution rules"""
    doc = """
    <div id="v"><span class="p-name">V</span></div>
    <div class="h-entry">
      <div class="e-content">
        <p>Text <a class="include" href="#v"></a><a href="/about">about</a></p>
      </div>
    </div>
    """
    result = parse_doc(doc, url="http://example.com/", include=True)
    content = result["items"][0]["properties"]["content"][0]
    # include element is still there, not the expanded venue
    assert '<a class="include"' in content["html"]
    assert "V</span>" not in content["html"]
    # ordinary URL resolution still applies inside e-*
    assert 'href="http://example.com/about"' in content["html"]
    assert content["value"] == "Text about"
    # includes inside e-* are not expanded and produce no diagnostics
    assert result["debug"]["include-diagnostics"] == []


def test_e_content_mf1_entry_content_unexpanded():
    """mf1 classes that backcompat maps to e-* also keep authored HTML"""
    doc = """
    <div id="v"><span class="p-name">V</span></div>
    <div class="hentry">
      <div class="entry-content"><p>Text <a class="include" href="#v"></a></p></div>
    </div>
    """
    result = parse_doc(doc, include=True)
    content = result["items"][0]["properties"]["content"][0]
    assert '<a class="include"' in content["html"]
    assert result["debug"]["include-diagnostics"] == []


def test_caller_dom_not_modified():
    """passing a BeautifulSoup document must not mutate it"""
    soup = BeautifulSoup(SHARED_VENUE, "html5lib")
    before = str(soup)
    parser = Parser(doc=soup, include=True)
    assert str(soup) == before
    # and the parse still expanded the includes internally
    events = [i for i in parser.to_dict()["items"] if "h-event" in i["type"]]
    assert events[0]["children"][0]["properties"]["name"] == ["The Grand Hall"]


def test_tag_input():
    """include mode also works when a single Tag is passed as document"""
    soup = BeautifulSoup(SHARED_VENUE, "html5lib")
    result = Parser(doc=soup.body, include=True).to_dict()
    events = [i for i in result["items"] if "h-event" in i["type"]]
    assert len(events) == 2
    assert events[0]["children"][0]["id"] == "venue"


def test_rels_collected_from_original_document():
    """rels come from the authored document, not the expanded copy"""
    doc = """
    <div id="v"><a rel="author" href="http://example.com/a">A</a></div>
    <div class="h-entry"><a class="include" href="#v"></a></div>
    <a rel="me" href="http://example.com/me">me</a>
    """
    result = parse_doc(doc, include=True)
    assert result["rels"]["me"] == ["http://example.com/me"]
    # the rel inside the referenced fragment appears exactly once
    # (collected from the original document only)
    assert result["rels"]["author"] == ["http://example.com/a"]


def test_parse_function_api_and_diagnostics_attribute():
    """the convenience parse() function exposes the same options, and the
    Parser object collects diagnostics on an attribute"""
    result = parse(SHARED_VENUE, include=True)
    events = [i for i in result["items"] if "h-event" in i["type"]]
    assert events[0]["children"][0]["id"] == "venue"

    parser = Parser('<a class="include" href="#gone"></a>', include=True)
    assert parser.include_diagnostics[0]["reason"] == "missing-id"
    assert (
        parser.to_dict()["debug"]["include-diagnostics"] == parser.include_diagnostics
    )


def test_object_fallback_content_not_expanded():
    """includes nested in the fallback content of a replaced object include
    are discarded together with the object"""
    doc = """
    <span id="x" class="p-tag">X</span>
    <span id="y" class="p-tag">Y</span>
    <div class="h-entry">
      <object class="include" data="#x"><a class="include" href="#y"></a></object>
    </div>
    """
    result = parse_doc(doc, include=True)
    entry = [i for i in result["items"] if "h-entry" in i["type"]][0]
    assert entry["properties"]["tag"] == ["X"]
