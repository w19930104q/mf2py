"""functions to parse the properties of elements"""

import re

from . import value_class_pattern
from .audit import make_leaf, make_source
from .datetime_helpers import DATETIME_RE, TIME_RE, normalize_datetime
from .dom_helpers import get_attr, get_img, get_textContent, try_urljoin


def text(el, base_url="", trace=None):
    """Process p-* properties

    When *trace* is a list, provenance describing how the value was obtained
    is appended to it (as a leaf dict) and the plain value is still returned.
    """

    # handle value-class-pattern
    prop_value = value_class_pattern.text(el)
    if prop_value is not None:
        if trace is not None:
            trace.append(
                make_leaf("p-value-class-pattern", value_class_pattern.text_sources(el))
            )
        return prop_value

    raw = get_attr(el, "title", check_name=("abbr", "link"))
    if raw is not None:
        rule, attr = "p-attr-title", "title"
        prop_value = raw
    else:
        raw = get_attr(el, "value", check_name=("data", "input"))
        if raw is not None:
            rule, attr = "p-attr-value", "value"
            prop_value = raw
        else:
            raw = get_attr(el, "alt", check_name=("img", "area"))
            if raw is not None:
                rule, attr = "p-attr-alt", "alt"
                prop_value = raw
            else:
                rule, attr = "p-text", None
                raw = get_textContent(el, replace_img=True, base_url=base_url)
                prop_value = raw

    if trace is not None:
        trace.append(make_leaf(rule, [make_source(el, raw, attr)]))
    return prop_value


def url(el, base_url="", trace=None):
    """Process u-* properties

    When *trace* is a list, provenance describing how the value was obtained
    (including the pre-normalisation URL) is appended to it.
    """

    def finish(raw, rule, sources):
        """Apply URL normalisation and record provenance if requested."""
        normalized = try_urljoin(base_url, raw)
        if trace is not None:
            trace.append(make_leaf(rule, sources))
        return normalized

    raw = get_attr(el, "href", check_name=("a", "area", "link"))
    if raw is not None:
        return finish(raw, "u-attr-href", [make_source(el, raw, "href")])

    img_value = get_img(el, base_url)
    if img_value is not None:
        # u-* on an <img>: get_img already normalised src; re-read the raw
        # attribute so the pre-normalisation value stays traceable
        sources = []
        raw_src = get_attr(el, "src", check_name="img")
        if raw_src is not None:
            sources.append(make_source(el, raw_src, "src"))
        if isinstance(img_value, dict):
            raw_alt = get_attr(el, "alt", check_name="img")
            if raw_alt is not None:
                sources.append(make_source(el, raw_alt, "alt"))
            raw_srcset = get_attr(el, "srcset", check_name="img")
            if raw_srcset:
                sources.append(make_source(el, raw_srcset, "srcset"))
        if trace is not None:
            trace.append(make_leaf("u-img-src", sources))
        return img_value

    raw = get_attr(el, "src", check_name=("audio", "video", "source", "iframe"))
    if raw is not None:
        return finish(raw, "u-attr-src", [make_source(el, raw, "src")])
    raw = get_attr(el, "poster", check_name="video")
    if raw is not None:
        return finish(raw, "u-attr-poster", [make_source(el, raw, "poster")])
    raw = get_attr(el, "data", check_name="object")
    if raw is not None:
        return finish(raw, "u-attr-data", [make_source(el, raw, "data")])

    # handle value-class-pattern
    vcp_value = value_class_pattern.text(el)
    if vcp_value is not None:
        if trace is not None:
            trace.append(
                make_leaf(
                    "u-value-class-pattern",
                    value_class_pattern.text_sources(el),
                )
            )
        return try_urljoin(base_url, vcp_value)

    raw = get_attr(el, "title", check_name="abbr")
    if raw is not None:
        return finish(raw, "u-attr-title", [make_source(el, raw, "title")])
    raw = get_attr(el, "value", check_name=("data", "input"))
    if raw is not None:
        return finish(raw, "u-attr-value", [make_source(el, raw, "value")])
    raw = get_textContent(el)
    return finish(raw, "u-text", [make_source(el, raw)])


def datetime(el, default_date=None, trace=None):
    """Process dt-* properties

    Args:
      el (bs4.element.Tag): Tag containing the dt-value

    Returns:
      a tuple (string string): a tuple of two strings, (datetime, date).
      When *trace* is a list, a provenance leaf is appended to it.
    """

    # handle value-class-pattern
    prop_value = value_class_pattern.datetime(el, default_date)
    if prop_value is not None:
        if trace is not None:
            trace.append(
                make_leaf(
                    "dt-value-class-pattern",
                    value_class_pattern.datetime_sources(el, default_date),
                )
            )
        return prop_value

    raw = get_attr(el, "datetime", check_name=("time", "ins", "del"))
    if raw is not None:
        rule, attr = "dt-attr-datetime", "datetime"
    elif (raw := get_attr(el, "title", check_name="abbr")) is not None:
        rule, attr = "dt-attr-title", "title"
    elif (raw := get_attr(el, "value", check_name=("data", "input"))) is not None:
        rule, attr = "dt-attr-value", "value"
    else:
        rule, attr = "dt-text", None
        raw = get_textContent(el)

    sources = [make_source(el, raw, attr)]

    # if this is just a time, augment with default date
    match = re.match(TIME_RE + "$", raw)
    if match and default_date:
        prop_value = "%s %s" % (default_date, raw)
        if trace is not None:
            # the default date itself was contributed by an earlier dt-*
            # property; record it as a synthetic contribution so the
            # composition stays traceable
            sources.append(make_source(el, default_date, attribute="default-date"))
            trace.append(make_leaf(rule + "-default-date", sources))
        return normalize_datetime(prop_value), default_date

    # otherwise, treat it as a full date
    match = re.match(DATETIME_RE + "$", raw)
    if trace is not None:
        trace.append(make_leaf(rule, sources))
    return (
        normalize_datetime(raw, match=match),
        match and match.group("date"),
    )


def embedded(el, base_url, root_lang, document_lang, expose_dom, trace=None):
    """Process e-* properties

    When *trace* is a list, provenance (the raw inner HTML and the raw text
    content before URL rewriting) is appended to it.
    """
    raw_html = el.decode_contents().strip()
    raw_value = get_textContent(el, replace_img=True, base_url=base_url)

    for tag in el.find_all():
        for attr in ("href", "src", "cite", "data", "poster"):
            if attr in tag.attrs:
                tag.attrs[attr] = try_urljoin(base_url, tag.attrs[attr])
    prop_value = {
        "value": get_textContent(el, replace_img=True, base_url=base_url),
    }
    if lang := el.attrs.get("lang"):
        prop_value["lang"] = lang
    elif root_lang:
        prop_value["lang"] = root_lang
    elif document_lang:
        prop_value["lang"] = document_lang
    if expose_dom:
        prop_value["dom"] = el
    else:
        prop_value["html"] = el.decode_contents().strip()

    if trace is not None:
        sources = [
            make_source(el, raw_value),
            make_source(el, raw_html, attribute="html"),
        ]
        trace.append(make_leaf("e-embedded", sources))
    return prop_value
