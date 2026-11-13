"""Regression tests for the optional field-provenance ("source-map") feature.

The feature is enabled with ``source_map=True`` and must satisfy:

* disabled by default, and the output is unchanged when disabled
* every properties-array member is addressed by a JSON Pointer that maps to
  the original DOM path(s) and the parsing rule that produced it
* duplicate or structurally identical values are never merged by reverse
  looking them up; each output member keeps its own trace
* nested h-* members, same-node multi-property elements and value-class
  pattern / value-title compositions are traced structurally
* composite datetimes list the date / time / timezone sources, including the
  legacy implied default-date rule
* URL traces record the pre-resolution value and the effective base URL
* a BeautifulSoup document passed in by the caller is never mutated by the
  tracing machinery
* to_dict / to_json / type filtering all keep the pointers aligned with the
  returned result
"""

import json
import os

import pytest
from bs4 import BeautifulSoup

from mf2py import Parser, parse
from mf2py.source_map import SourceMap, dom_path

TEST_DIR = "test/examples/"


def parse_fixture(path, **kwargs):
    with open(os.path.join(TEST_DIR, path)) as f:
        return Parser(doc=f, html_parser="html5lib", **kwargs)


def resolve(doc, pointer):
    """Resolve an RFC 6901 JSON Pointer against a document."""
    node = doc
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


@pytest.fixture
def regression():
    return parse_fixture(
        "source_map_regression.html",
        source_map=True,
        url="http://example.com/",
    )


# ---------------------------------------------------------------------------
# option gating
# ---------------------------------------------------------------------------


def test_disabled_by_default():
    p = parse_fixture("simple_person_reference.html")
    assert "source-map" not in p.to_dict()
    assert p.source_map() == {}


def test_enabled_output_values_unchanged():
    on = parse_fixture("nested_multiple_classnames.html", source_map=True)
    off = parse_fixture("nested_multiple_classnames.html")
    on_dict = on.to_dict()
    off_dict = off.to_dict()
    assert "source-map" in on_dict
    # stripping the provenance key restores the ordinary output exactly
    del on_dict["source-map"]
    assert on_dict == off_dict


def test_parse_function_accepts_source_map():
    with open(os.path.join(TEST_DIR, "simple_person_reference.html")) as f:
        result = parse(doc=f, source_map=True)
    assert "source-map" in result


# ---------------------------------------------------------------------------
# pointer integrity
# ---------------------------------------------------------------------------


def test_every_pointer_resolves_against_the_document(regression):
    document = regression.to_dict()
    for pointer in document["source-map"]:
        # must not raise
        resolve(document, pointer)


def test_pointers_cover_every_property_member(regression):
    document = regression.to_dict()
    source_map = document["source-map"]

    def walk(item, prefix):
        for prop_name, values in item.get("properties", {}).items():
            for index, value in enumerate(values):
                member_pointer = "%s/properties/%s/%d" % (
                    prefix,
                    prop_name.replace("~", "~0").replace("/", "~1"),
                    index,
                )
                assert member_pointer in source_map, member_pointer
                if isinstance(value, dict) and "properties" in value:
                    walk(value, member_pointer)
        for index, child in enumerate(item.get("children", [])):
            walk(child, "%s/children/%d" % (prefix, index))

    for index, item in enumerate(document["items"]):
        walk(item, "/items/%d" % index)


def test_traces_are_json_serialisable(regression):
    json.dumps(regression.to_dict())
    json.dumps(regression.source_map())


# ---------------------------------------------------------------------------
# nested h-* and same-node multiple properties
# ---------------------------------------------------------------------------


def test_nested_microformat_trace(regression):
    source_map = regression.source_map()
    author = source_map["/items/0/properties/author/0"][0]
    # the nested h-card root itself is traced structurally
    assert author["paths"] == ["/html[1]/body[1]/article[1]/div[1]"]

    nested_name = source_map["/items/0/properties/author/0/properties/name/0"][0]
    assert nested_name["paths"] == ["/html[1]/body[1]/article[1]/div[1]/span[1]"]
    assert nested_name["value"] == "Jane Doe"

    # the simple "value" folded onto the nested microformat
    simple = source_map["/items/0/properties/author/0/value"][0]
    assert simple["paths"] == ["/html[1]/body[1]/article[1]/div[1]/span[1]"]


def test_same_node_multiple_properties_share_path_not_trace(regression):
    source_map = regression.source_map()
    name = source_map["/items/0/properties/name/1"][0]
    syndication = source_map["/items/0/properties/syndication/0"][0]
    # the anchor carries both p-name and u-syndication
    anchor = "/html[1]/body[1]/article[1]/a[2]"
    assert name["paths"] == [anchor]
    assert syndication["paths"] == [anchor]
    # distinct trace records, different rules
    assert name["kind"] == "text"
    assert syndication["kind"] == "attribute"
    assert syndication["attribute"] == "href"


def test_repeated_property_names_keep_independent_traces(regression):
    document = regression.to_dict()
    source_map = document["source-map"]
    values = document["items"][0]["properties"]["category"]
    assert values == ["cats", "cats"]  # identical authored values
    first = source_map["/items/0/properties/category/0"][0]
    second = source_map["/items/0/properties/category/1"][0]
    # no reverse look-up merging: distinct DOM paths
    assert first["paths"] == ["/html[1]/body[1]/article[1]/span[3]"]
    assert second["paths"] == ["/html[1]/body[1]/article[1]/span[4]"]


def test_structurally_equal_elements_get_distinct_paths():
    soup = BeautifulSoup(
        '<html><body><div><span class="p-x">a</span>'
        '<span class="p-x">a</span></div></body></html>',
        "html5lib",
    )
    spans = soup.find_all("span")
    assert spans[0] == spans[1]  # bs4 equality collapses them
    assert dom_path(spans[0]) != dom_path(spans[1])
    assert dom_path(spans[0]).endswith("span[1]")
    assert dom_path(spans[1]).endswith("span[2]")


# ---------------------------------------------------------------------------
# value-class-pattern, value-title and composite datetimes
# ---------------------------------------------------------------------------


def test_split_datetime_lists_each_part(regression):
    trace = regression.source_map()["/items/0/properties/published/0"][0]
    assert trace["kind"] == "value-class-pattern"
    roles = {part["role"]: part for part in trace["parts"]}
    assert set(roles) == {"date", "time", "timezone"}
    assert roles["date"]["value"] == "2024-05-21"
    assert roles["date"]["paths"] == ["/html[1]/body[1]/article[1]/span[1]/data[1]"]
    assert roles["time"]["value"] == "18:30"
    assert roles["timezone"]["value"] == "-06:00"
    # the top-level paths gather every contributing element
    assert len(trace["paths"]) == 3


def test_implied_default_date_is_marked(regression):
    trace = regression.source_map()["/items/0/properties/updated/0"][0]
    assert trace["kind"] == "composite-datetime"
    date_part = next(part for part in trace["parts"] if part["role"] == "date")
    time_part = next(part for part in trace["parts"] if part["role"] == "time")
    assert date_part["from-default-date"] is True
    assert date_part["kind"] == "implied"
    # the borrowed date points at the earlier dt-published element
    assert date_part["paths"] == ["/html[1]/body[1]/article[1]/span[1]"]
    assert time_part["paths"] == ["/html[1]/body[1]/article[1]/span[2]"]


def test_value_title_attribute_trace(regression):
    trace = regression.source_map()["/items/0/properties/duration/0"][0]
    assert trace["kind"] == "attribute"
    assert trace["attribute"] == "title"
    assert trace["value"] == "PT1H"
    assert trace["paths"] == ["/html[1]/body[1]/article[1]/abbr[1]"]


def test_embedded_property_trace(regression):
    trace = regression.source_map()["/items/0/properties/content/0"][0]
    assert trace["kind"] == "embedded"
    assert trace["paths"] == ["/html[1]/body[1]/article[1]/div[2]"]
    assert trace["base-url"] == "http://example.com/"


# ---------------------------------------------------------------------------
# URLs: pre-resolution value and effective base
# ---------------------------------------------------------------------------


def test_url_trace_records_raw_value_and_base(regression):
    document = regression.to_dict()
    trace = document["source-map"]["/items/0/properties/url/0"][0]
    assert trace["attribute"] == "href"
    assert trace["value"] == "/posts/1"  # before resolution
    assert trace["base-url"] == "http://example.com/"
    # the emitted value is the resolved URL
    assert document["items"][0]["properties"]["url"][0] == (
        "http://example.com/posts/1"
    )


def test_absolute_url_passes_through_base(regression):
    trace = regression.source_map()["/items/0/properties/syndication/0"][0]
    assert trace["value"] == "https://news.example.com/x"
    assert trace["base-url"] == "http://example.com/"


# ---------------------------------------------------------------------------
# implied properties
# ---------------------------------------------------------------------------


def test_implied_properties_are_marked(regression):
    source_map = regression.source_map()
    name = source_map["/items/1/properties/name/0"][0]
    photo = source_map["/items/1/properties/photo/0"][0]
    url = source_map["/items/1/properties/url/0"][0]
    assert name["kind"] == "implied"
    assert photo["kind"] == "implied"
    assert url["kind"] == "implied"
    assert name["attribute"] == "alt"
    assert photo["attribute"] == "src"
    assert url["attribute"] == "href"
    assert url["value"] == "/people/implicit"


# ---------------------------------------------------------------------------
# microformats1 back-compat
# ---------------------------------------------------------------------------


def test_backcompat_root_and_property_mapping(regression):
    source_map = regression.source_map()
    root = source_map["/items/2"][0]
    assert root["kind"] == "backcompat"
    assert root["backcompat-root"] == ["vcard"]
    assert root["mapping"] == {"vcard": ["h-card"]}
    assert root["paths"] == ["/html[1]/body[1]/div[2]"]

    name = source_map["/items/2/properties/name/0"][0]
    assert name["kind"] == "backcompat"
    assert name["mapping"] == {"classes": ["fn"], "properties": ["p-name"]}
    # path still resolves against the authored document
    assert name["paths"] == ["/html[1]/body[1]/div[2]/span[1]"]

    url = source_map["/items/2/properties/url/0"][0]
    assert url["mapping"] == {"classes": ["url"], "properties": ["u-url"]}
    assert url["paths"] == ["/html[1]/body[1]/div[2]/a[1]"]


def test_backcompat_rel_tag_synthetic_data_points_at_anchor():
    p = parse_fixture("backcompat/hentry_with_rel_tag.html", source_map=True)
    source_map = p.source_map()
    trace = source_map["/items/0/properties/category/0"][0]
    assert trace["kind"] == "backcompat"
    assert trace["mapping"] == {
        "rels": ["tag"],
        "properties": ["p-category"],
    }
    # the synthesised <data> traces back to the authored <a rel=tag>
    assert trace["paths"][0].endswith("article[1]/a[1]")
    assert trace["paths"][0].startswith("/html")


# ---------------------------------------------------------------------------
# DOM immutability
# ---------------------------------------------------------------------------


def test_input_soup_is_not_mutated():
    for fixture, kwargs in (
        ("ordering_dedup.html", {"url": "http://example.com/"}),
        ("backcompat/hentry.html", {}),
        ("source_map_regression.html", {"url": "http://example.com/"}),
    ):
        with open(os.path.join(TEST_DIR, fixture)) as f:
            soup = BeautifulSoup(f.read(), "html5lib")
        before = str(soup)
        Parser(doc=soup, html_parser="html5lib", source_map=True, **kwargs)
        assert str(soup) == before, fixture


# ---------------------------------------------------------------------------
# to_dict / to_json / filtering alignment
# ---------------------------------------------------------------------------


def test_to_json_embeds_source_map(regression):
    decoded = json.loads(regression.to_json())
    assert "source-map" in decoded
    assert decoded["source-map"] == regression.source_map()


def test_filtered_source_map_is_rebased_and_resolves():
    p = parse_fixture("festivus.html", source_map=True)
    cards = p.to_dict(filter_by_type="h-card")
    source_map = p.source_map(filter_by_type="h-card")
    assert cards  # sanity
    # rebased pointers address the bare list, no /items prefix
    assert all(not pointer.startswith("/items") for pointer in source_map)
    for pointer in source_map:
        resolve(cards, pointer)
    # and the unfiltered map keeps full document pointers
    assert any(pointer.startswith("/items/") for pointer in p.source_map())


def test_filtering_keeps_traces_aligned_with_values():
    p = parse_fixture("festivus.html", source_map=True)
    cards = p.to_dict(filter_by_type="h-card")
    source_map = p.source_map(filter_by_type="h-card")
    for index, card in enumerate(cards):
        name = card["properties"]["name"][0]
        trace = source_map["/%d/properties/name/0" % index][0]
        assert trace["value"] == name


def test_source_map_renders_independent_of_output_ordering():
    # values and traces are produced in the same append order
    p = parse_fixture("ordering_dedup.html", source_map=True)
    document = p.to_dict()
    names = document["items"][0]["properties"]["name"]
    traces = [
        document["source-map"]["/items/0/properties/name/%d" % i][0]
        for i in range(len(names))
    ]
    assert [trace["value"] for trace in traces] == names


# ---------------------------------------------------------------------------
# metaformats
# ---------------------------------------------------------------------------


def test_metaformats_traces():
    p = parse_fixture("metaformats_ogp.html", source_map=True, metaformats=True)
    source_map = p.source_map()
    trace = source_map["/items/0/properties/name/0"][0]
    assert trace["kind"] == "metaformats"
    assert trace["attribute"] == "content"
    assert trace["paths"][0].startswith("/html[1]/head[1]/meta[")
