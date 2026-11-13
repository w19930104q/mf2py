"""Tests for the opt-in provenance/audit interface.

The audit trail is only collected when a Parser is built with ``audit=True``;
these tests check that provenance entries correspond one-to-one with the
values actually returned, covering nested entities, duplicate values from
different nodes, implied properties, combined value-class-pattern datetimes,
MF1 backcompat (including synthetic rel=tag elements) and type filtering.
"""

import json
import os

import pytest
from bs4 import BeautifulSoup

from mf2py import Parser, parse

FIXTURE = os.path.join("test", "examples", "audit", "provenance.html")
BASE_URL = "https://example.com/"


def _parser(**kwargs):
    with open(FIXTURE) as f:
        return Parser(doc=f, html_parser="html5lib", url=BASE_URL, audit=True, **kwargs)


def _by_pointer(entries):
    return {e["pointer"]: e for e in entries}


def _resolve(document, pointer):
    """Minimal RFC 6901 resolver used to prove pointers hit real values."""
    node = document
    for token in pointer.lstrip("/").split("/"):
        if token == "":
            continue
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(token)]
        else:
            node = node[token]
    return node


# --------------------------------------------------------------------------
# opt-in behaviour and isolation from the regular result
# --------------------------------------------------------------------------


def test_audit_is_off_by_default_and_result_is_unmarked():
    with open(FIXTURE) as f:
        p = Parser(doc=f, html_parser="html5lib", url=BASE_URL)
    assert p.audit_enabled is False
    serialized = json.loads(p.to_json())
    # no audit container and no internal markers on business fields
    assert "audit" not in serialized
    assert "document" not in serialized
    text_blob = json.dumps(serialized)
    assert "_audit" not in text_blob
    assert "node_path" not in text_blob
    with pytest.raises(RuntimeError):
        p.audit()


def test_audit_does_not_change_parse_result():
    with open(FIXTURE) as f:
        plain = parse(f, url=BASE_URL, html_parser="html5lib")
    with open(FIXTURE) as f:
        audited = Parser(
            doc=f, html_parser="html5lib", url=BASE_URL, audit=True
        ).to_dict()
    assert plain == audited


def test_to_json_without_include_audit_is_unchanged():
    p = _parser()
    assert json.loads(p.to_json()) == p.to_dict()


def test_to_json_include_audit_wraps_document_and_audit():
    p = _parser()
    wrapped = json.loads(p.to_json(include_audit=True))
    assert set(wrapped) == {"document", "audit"}
    assert wrapped["document"] == p.to_dict()
    assert wrapped["audit"] == p.audit()


# --------------------------------------------------------------------------
# every entry points at a real value in the result
# --------------------------------------------------------------------------


def test_every_pointer_resolves_to_the_audited_value():
    p = _parser()
    document = p.to_dict()
    entries = p.audit()
    assert entries  # the fixture must actually exercise provenance
    for entry in entries:
        assert entry["implied"] in (True, False)
        assert entry["sources"], entry
        for source in entry["sources"]:
            assert source["node_path"]
            assert "value" in source
        # the pointer lands on the exact value the entry describes
        resolved = _resolve(document, entry["pointer"])
        if isinstance(resolved, dict):
            # e-* / img values are objects; pointer still identifies the element
            assert "value" in resolved
        else:
            assert resolved is not None


def test_one_entry_per_properties_array_element_including_nested():
    p = _parser()
    entries = p.audit()
    pointers = [e["pointer"] for e in entries]
    # no duplicate pointers: each properties-array element gets one entry
    assert len(pointers) == len(set(pointers))

    by_pointer = _by_pointer(entries)
    # nested h-card author properties are audited with a full path
    author_name = by_pointer["/items/0/properties/author/0/properties/name/0"]
    assert author_name["sources"][0]["value"] == "Alice"
    assert author_name["rule"] == "p-text"
    assert _resolve(p.to_dict(), author_name["pointer"]) == "Alice"


# --------------------------------------------------------------------------
# duplicate values from different nodes are not merged
# --------------------------------------------------------------------------


def test_identical_text_from_different_nodes_stays_distinct():
    p = _parser()
    by_pointer = _by_pointer(p.audit())
    summary = by_pointer["/items/0/properties/summary/0"]
    category = by_pointer["/items/0/properties/category/0"]
    assert summary["sources"][0]["value"] == "same text"
    assert category["sources"][0]["value"] == "same text"
    # same text, different nodes, different paths
    assert summary["sources"][0]["node_path"] != category["sources"][0]["node_path"]
    assert "p-summary" in summary["sources"][0]["node_path"]
    assert "p-category" in category["sources"][0]["node_path"]


# --------------------------------------------------------------------------
# url normalisation: the pre-normalisation value remains traceable
# --------------------------------------------------------------------------


def test_url_sources_keep_pre_normalisation_value():
    p = _parser()
    by_pointer = _by_pointer(p.audit())
    url_entry = by_pointer["/items/0/properties/url/0"]
    assert url_entry["rule"] == "u-attr-href"
    assert url_entry["sources"][0]["attribute"] == "href"
    # raw, relative value is preserved in the source
    assert url_entry["sources"][0]["value"] == "/posts/audit"
    # while the returned result is normalised
    assert (
        _resolve(p.to_dict(), url_entry["pointer"]) == "https://example.com/posts/audit"
    )


def test_img_url_records_src_and_alt_separately():
    p = _parser()
    by_pointer = _by_pointer(p.audit())
    photo = by_pointer["/items/0/properties/photo/0"]
    assert photo["rule"] == "u-img-src"
    attributes = {s["attribute"]: s["value"] for s in photo["sources"]}
    assert attributes["src"] == "/img/audit.png"  # pre-normalisation
    assert attributes["alt"] == "Audit photo"
    result = _resolve(p.to_dict(), photo["pointer"])
    assert result["value"] == "https://example.com/img/audit.png"


# --------------------------------------------------------------------------
# combined value-class-pattern datetime lists contributors in order
# --------------------------------------------------------------------------


def test_combined_datetime_lists_each_contribution_in_order():
    p = _parser()
    by_pointer = _by_pointer(p.audit())
    start = by_pointer["/items/0/properties/start/0"]
    assert start["rule"] == "dt-value-class-pattern"
    raw_parts = [s["value"] for s in start["sources"]]
    assert raw_parts == ["2026-11-13", "18:30", "+09:00"]
    # distinct node paths, in document order
    paths = [s["node_path"] for s in start["sources"]]
    assert len(set(paths)) == 3
    assert all("span.value" in path for path in paths)
    # the combined value is what the result actually holds
    assert _resolve(p.to_dict(), start["pointer"]).startswith("2026-11-13 18:30")


def test_value_title_source_uses_title_attribute():
    p = _parser()
    location = _by_pointer(p.audit())["/items/0/properties/location/0"]
    assert location["rule"] == "p-value-class-pattern"
    assert location["sources"][0]["attribute"] == "title"
    assert location["sources"][0]["value"] == "Tokyo"


# --------------------------------------------------------------------------
# implied properties are explicitly marked
# --------------------------------------------------------------------------


def test_implied_properties_are_marked_and_sourced():
    p = _parser()
    by_pointer = _by_pointer(p.audit())

    implied_photo = by_pointer["/items/1/properties/photo/0"]
    assert implied_photo["implied"] is True
    assert implied_photo["rule"].startswith("implied-photo")
    assert implied_photo["sources"][0]["attribute"] == "src"
    assert implied_photo["sources"][0]["value"] == "/img/implicit.png"

    implied_url = by_pointer["/items/1/properties/url/0"]
    assert implied_url["implied"] is True
    assert implied_url["rule"].startswith("implied-url")
    assert implied_url["sources"][0]["value"] == "/people/bob"

    # explicit entries are never flagged as implied
    explicit_name = by_pointer["/items/0/properties/name/0"]
    assert explicit_name["implied"] is False


# --------------------------------------------------------------------------
# e-* embedded markup keeps both text and raw html
# --------------------------------------------------------------------------


def test_embedded_property_records_text_and_raw_html():
    p = _parser()
    content = _by_pointer(p.audit())["/items/0/properties/content/0"]
    assert content["rule"] == "e-embedded"
    kinds = {s.get("attribute"): s["value"] for s in content["sources"]}
    assert kinds[None] == "Hello world"
    assert kinds["html"] == 'Hello <a href="/related">world</a>'


# --------------------------------------------------------------------------
# MF1 backcompat: mappings flagged, synthetic rel=tag traced to original node
# --------------------------------------------------------------------------


def test_mf1_class_mapping_is_flagged():
    p = _parser()
    by_pointer = _by_pointer(p.audit())
    name = by_pointer["/items/2/properties/name/0"]
    assert name["mf1"] == {"root": "hentry", "class": "entry-title"}
    assert name["sources"][0]["node_path"].endswith("span.entry-title[1]")


def test_mf1_nested_entity_is_audited():
    p = _parser()
    by_pointer = _by_pointer(p.audit())
    nested_name = by_pointer["/items/2/children/0/properties/name/0"]
    assert nested_name["mf1"] == {"root": "vcard", "class": "fn"}
    assert nested_name["sources"][0]["value"] == "Carol"
    nested_url = by_pointer["/items/2/children/0/properties/url/0"]
    assert nested_url["mf1"] == {"root": "vcard", "class": "url"}
    # pre-normalisation href retained
    assert nested_url["sources"][0]["value"] == "/carol"


def test_mf1_rel_tag_synthetic_element_traces_original_anchor():
    p = _parser()
    category = _by_pointer(p.audit())["/items/2/properties/category/0"]
    assert category["mf1"] == {"root": "hentry", "rel": "tag"}
    source = category["sources"][0]
    # value comes from the synthesised data tag's value attribute...
    assert source["attribute"] == "value"
    assert source["value"] == "cat"
    # ...but the node path resolves back to the original <a rel=tag>
    assert source["node_path"].endswith("a[2]")
    assert "data" not in source["node_path"]


def test_mf1_embedded_content_resolves_to_original_node():
    p = _parser()
    content = _by_pointer(p.audit())["/items/2/properties/content/0"]
    assert content["mf1"] == {"root": "hentry", "class": "entry-content"}
    assert content["sources"][0]["node_path"].endswith("div.entry-content[3]")


# --------------------------------------------------------------------------
# filter_by_type: pointers must match the filtered result
# --------------------------------------------------------------------------


def test_filtered_audit_pointers_match_filtered_result():
    p = _parser()
    filtered = p.to_dict(filter_by_type="h-card")
    entries = p.audit(filter_by_type="h-card")

    # the fixture has two h-card items (one standalone, one nested in hentry
    # is a child, not top-level); top-level h-card count:
    assert len(filtered) == 1
    for entry in entries:
        # pointers are relative to the filtered list, starting at /0
        assert entry["pointer"].startswith("/0/")
        resolved = _resolve(filtered, entry["pointer"])
        assert resolved is not None

    by_pointer = _by_pointer(entries)
    assert "/0/properties/photo/0" in by_pointer
    assert by_pointer["/0/properties/photo/0"]["implied"] is True


def test_filtered_audit_excludes_other_types():
    p = _parser()
    entries = p.audit(filter_by_type="h-entry")
    # all pointers stay inside the two h-entry items (indices 0 and 1 of the
    # filtered list); no h-card-only pointer may appear
    assert entries
    for entry in entries:
        assert entry["pointer"].startswith(("/0/", "/1/"))
    # h-entry filtered list contains both top-level entries
    assert len(p.to_dict(filter_by_type="h-entry")) == 2


# --------------------------------------------------------------------------
# additional rule coverage
# --------------------------------------------------------------------------


def test_img_srcset_records_raw_srcset_alongside_src_and_alt():
    html = (
        '<div class="h-card">'
        '<img class="u-photo" src="/p.png" '
        'srcset="/p1.png 1x, /p2.png 2x" alt="P" /></div>'
    )
    p = Parser(doc=html, url="http://e.com/", audit=True)
    photo = _by_pointer(p.audit())["/items/0/properties/photo/0"]
    by_attr = {s["attribute"]: s["value"] for s in photo["sources"]}
    # all three raw contributions kept, none normalised in the source values
    assert by_attr["src"] == "/p.png"
    assert by_attr["alt"] == "P"
    assert by_attr["srcset"] == "/p1.png 1x, /p2.png 2x"


def test_embedded_path_resolves_with_preserved_document():
    # when given a BeautifulSoup document the parser works on a copy for e-*
    # properties; provenance must still point at the original DOM node
    soup = BeautifulSoup(
        '<div class="h-card"><div class="e-content">hi</div></div>',
        "html5lib",
    )
    p = Parser(soup, audit=True, expose_dom=True)
    content = _by_pointer(p.audit())["/items/0/properties/content/0"]
    node_path = content["sources"][0]["node_path"]
    assert node_path
    assert node_path.endswith("div.e-content[1]")


def test_default_date_inheritance_is_traceable():
    html = (
        '<article class="h-entry">'
        '<time class="dt-published" datetime="2015-01-02 03:04:05Z">d</time>'
        '<time class="dt-updated">05:06</time>'
        "</article>"
    )
    p = Parser(doc=html, audit=True)
    updated = _by_pointer(p.audit())["/items/0/properties/updated/0"]
    assert updated["rule"] == "dt-text-default-date"
    by_attr = {s.get("attribute"): s["value"] for s in updated["sources"]}
    # the bare time and the inherited date are both traceable
    assert by_attr[None] == "05:06"
    assert by_attr["default-date"] == "2015-01-02"
    assert _resolve(p.to_dict(), updated["pointer"]) == "2015-01-02 05:06"
