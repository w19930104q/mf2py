"""functions to parse the properties of elements"""

import re

from . import value_class_pattern
from .datetime_helpers import DATETIME_RE, TIME_RE, normalize_datetime
from .dom_helpers import get_attr, get_img, get_textContent, try_urljoin


def text(el, base_url="", trace=False):
    """Process p-* properties"""

    # handle value-class-pattern
    if trace:
        vcp = value_class_pattern.text(el, trace=True)
        if vcp is not None:
            return vcp
    else:
        prop_value = value_class_pattern.text(el)
        if prop_value is not None:
            return prop_value

    prop_value = get_attr(el, "title", check_name=("abbr", "link"))
    if prop_value is not None:
        if trace:
            return prop_value, {
                "kind": "attribute",
                "attribute": "title",
                "element": el,
                "value": prop_value,
            }
    if prop_value is None:
        prop_value = get_attr(el, "value", check_name=("data", "input"))
        if prop_value is not None and trace:
            return prop_value, {
                "kind": "attribute",
                "attribute": "value",
                "element": el,
                "value": prop_value,
            }
    if prop_value is None:
        prop_value = get_attr(el, "alt", check_name=("img", "area"))
        if prop_value is not None and trace:
            return prop_value, {
                "kind": "attribute",
                "attribute": "alt",
                "element": el,
                "value": prop_value,
            }
    if prop_value is None:
        prop_value = get_textContent(el, replace_img=True, base_url=base_url)
        if trace:
            return prop_value, {
                "kind": "text",
                "element": el,
                "value": prop_value,
            }

    return prop_value


def url(el, base_url="", trace=False):
    """Process u-* properties"""

    raw_value = None
    source_info = None

    prop_value = get_attr(el, "href", check_name=("a", "area", "link"))
    if prop_value is not None:
        raw_value = prop_value
        source_info = {"kind": "attribute", "attribute": "href", "element": el}
    if prop_value is None:
        prop_value = get_img(el, base_url)
        if prop_value is not None:
            if trace:
                src = get_attr(el, "src", check_name="img")
                return prop_value, {
                    "kind": "attribute",
                    "attribute": "src",
                    "element": el,
                    "value": src,
                    "base-url": base_url,
                }
            return prop_value
    if prop_value is None:
        prop_value = get_attr(
            el, "src", check_name=("audio", "video", "source", "iframe")
        )
        if prop_value is not None:
            raw_value = prop_value
            source_info = {"kind": "attribute", "attribute": "src", "element": el}
    if prop_value is None:
        prop_value = get_attr(el, "poster", check_name="video")
        if prop_value is not None:
            raw_value = prop_value
            source_info = {
                "kind": "attribute",
                "attribute": "poster",
                "element": el,
            }
    if prop_value is None:
        prop_value = get_attr(el, "data", check_name="object")
        if prop_value is not None:
            raw_value = prop_value
            source_info = {"kind": "attribute", "attribute": "data", "element": el}

    if prop_value is None:
        # handle value-class-pattern
        if trace:
            vcp = value_class_pattern.text(el, trace=True)
            if vcp is not None:
                value, infos = vcp
                resolved = try_urljoin(base_url, value)
                infos = [dict(info, **{"base-url": base_url}) for info in infos]
                return resolved, {
                    "kind": "value-class-pattern",
                    "element": el,
                    "value": value,
                    "base-url": base_url,
                    "parts": infos,
                }
        else:
            prop_value = value_class_pattern.text(el)

    if prop_value is None:
        prop_value = get_attr(el, "title", check_name="abbr")
        if prop_value is not None:
            raw_value = prop_value
            source_info = {"kind": "attribute", "attribute": "title", "element": el}
    if prop_value is None:
        prop_value = get_attr(el, "value", check_name=("data", "input"))
        if prop_value is not None:
            raw_value = prop_value
            source_info = {"kind": "attribute", "attribute": "value", "element": el}
    if prop_value is None:
        prop_value = get_textContent(el)
        raw_value = prop_value
        source_info = {"kind": "text", "element": el}

    resolved = try_urljoin(base_url, prop_value)
    if trace:
        source_info = dict(source_info)
        source_info["value"] = raw_value
        source_info["base-url"] = base_url
        return resolved, source_info
    return resolved


def datetime(el, default_date=None, trace=False, default_date_source=None):
    """Process dt-* properties

    Args:
      el (bs4.element.Tag): Tag containing the dt-value

    Returns:
      a tuple (string string): a tuple of two strings, (datetime, date). When
      trace is True a third element with source information is returned.
    """

    # handle value-class-pattern
    if trace:
        vcp = value_class_pattern.datetime(
            el,
            default_date,
            trace=True,
            default_date_source=default_date_source,
        )
        if vcp is not None:
            dt_value, new_date, parts = vcp
            return (
                dt_value,
                new_date,
                {
                    "kind": "value-class-pattern",
                    "element": el,
                    "value": dt_value,
                    "parts": parts,
                },
            )
    else:
        prop_value = value_class_pattern.datetime(el, default_date)
        if prop_value is not None:
            return prop_value

    source_info = None
    prop_value = get_attr(el, "datetime", check_name=("time", "ins", "del"))
    if prop_value is not None:
        source_info = {
            "kind": "attribute",
            "attribute": "datetime",
            "element": el,
            "value": prop_value,
        }
    if prop_value is None:
        prop_value = get_attr(el, "title", check_name="abbr")
        if prop_value is not None:
            source_info = {
                "kind": "attribute",
                "attribute": "title",
                "element": el,
                "value": prop_value,
            }
    if prop_value is None:
        prop_value = get_attr(el, "value", check_name=("data", "input"))
        if prop_value is not None:
            source_info = {
                "kind": "attribute",
                "attribute": "value",
                "element": el,
                "value": prop_value,
            }
    if prop_value is None:
        prop_value = get_textContent(el)
        source_info = {"kind": "text", "element": el, "value": prop_value}

    # if this is just a time, augment with default date
    match = re.match(TIME_RE + "$", prop_value)
    if match and default_date:
        augmented = "%s %s" % (default_date, prop_value)
        result = normalize_datetime(augmented)
        if trace:
            return (
                result,
                default_date,
                {
                    "kind": "composite-datetime",
                    "element": el,
                    "value": result,
                    "parts": [
                        {
                            "kind": "implied",
                            "role": "date",
                            "value": default_date,
                            "from-default-date": True,
                            "element": default_date_source,
                        },
                        {
                            **source_info,
                            "role": "time",
                        },
                    ],
                },
            )
        return result, default_date

    # otherwise, treat it as a full date
    match = re.match(DATETIME_RE + "$", prop_value)
    result = normalize_datetime(prop_value, match=match)
    new_date = match and match.group("date")
    if trace:
        return result, new_date, source_info
    return result, new_date


def embedded(el, base_url, root_lang, document_lang, expose_dom, trace=False):
    """Process e-* properties"""
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
    if trace:
        return prop_value, {
            "kind": "embedded",
            "element": el,
            "value": prop_value["value"],
            "base-url": base_url,
        }
    return prop_value
