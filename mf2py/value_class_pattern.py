"""functions to parse the properties of elements according to the value class pattern http://microformats.org/wiki/value-class-pattern """

import re

from .audit import make_source
from .datetime_helpers import (
    DATE_RE,
    DATETIME_RE,
    TIME_RE,
    TIMEZONE_RE,
    normalize_datetime,
)
from .dom_helpers import get_children


def _get_vcp_value(el):
    if "value-title" in el.get("class", []):
        return el.get("title")
    return el.get_text()


def _get_vcp_children(el):
    return [
        c
        for c in get_children(el)
        if c.has_attr("class")
        and ("value" in c["class"] or "value-title" in c["class"])
    ]


def text(el):
    value_els = _get_vcp_children(el)
    if value_els:
        return "".join(_get_vcp_value(el) for el in value_els)


def text_sources(el):
    """Provenance for :func:`text`: one source per contributing value
    element, in document order, with raw ``title``/text values."""
    sources = []
    for value_el in _get_vcp_children(el):
        if "value-title" in value_el.get("class", []):
            sources.append(
                make_source(value_el, value_el.get("title"), attribute="title")
            )
        else:
            sources.append(make_source(value_el, value_el.get_text()))
    return sources


def _datetime_part(value_el):
    """Replicate the per-element extraction used by the value class
    pattern, returning the raw date-part string that gets consumed."""
    if "value-title" in value_el.get("class", []):
        title = value_el.get("title")
        if title:
            return title.strip()
    elif value_el.name in ("img", "area"):
        alt = value_el.get("alt") or value_el.get_text()
        if alt:
            return alt.strip()
    elif value_el.name == "data":
        val = value_el.get("value") or value_el.get_text()
        if val:
            return val.strip()
    elif value_el.name == "abbr":
        title = value_el.get("title") or value_el.get_text()
        if title:
            return title.strip()
    elif value_el.name in ("del", "ins", "time"):
        dt = value_el.get("datetime") or value_el.get_text()
        if dt:
            return dt.strip()
    else:
        val = value_el.get_text()
        if val:
            return val.strip()
    return None


def _collect_datetime_parts(el, default_date):
    """Shared implementation behind :func:`datetime` and
    :func:`datetime_sources`.

    Returns ``(result_tuple_or_None, contributions)`` where contributions is
    a list of ``(value_el, raw_part, role)`` in contribution order; role is
    one of ``"datetime"``, ``"date"``, ``"time"``, ``"timezone"``. A
    ``(None, "default-date")`` contribution marks use of *default_date*.
    """
    value_els = _get_vcp_children(el)
    if not value_els:
        return None, []

    contributions = []
    date_part = time_part = tz_part = None

    for value_el in value_els:
        part = _datetime_part(value_el)
        if part is None:
            continue
        match = re.match(DATETIME_RE + "$", part)
        if match:
            # if it's a full datetime, then we're done
            date_part = match.group("date")
            contributions.append((value_el, part, "datetime"))
            result = (normalize_datetime(part, match=match), date_part)
            return result, contributions

        # only use first found value
        if re.match(TIME_RE + "$", part) and time_part is None:
            time_part = part
            contributions.append((value_el, part, "time"))
        elif re.match(DATE_RE + "$", part) and date_part is None:
            date_part = part
            contributions.append((value_el, part, "date"))
        elif re.match(TIMEZONE_RE + "$", part) and tz_part is None:
            tz_part = part
            contributions.append((value_el, part, "timezone"))

    # use default date
    if date_part is None:
        date_part = default_date
        if default_date is not None:
            contributions.append((None, default_date, "default-date"))

    if date_part and time_part:
        date_time_value = "%s %s" % (date_part, time_part)
    else:
        date_time_value = date_part or time_part

    if tz_part:
        date_time_value += tz_part

    return (normalize_datetime(date_time_value), date_part), contributions


def datetime(el, default_date=None):
    result, _ = _collect_datetime_parts(el, default_date)
    return result


def datetime_sources(el, default_date=None):
    """Provenance for :func:`datetime`: one source per contributing value
    element, in contribution order, each annotated with the part role
    (``datetime``/``date``/``time``/``timezone``) it provided. Use of the
    inherited default date is included with attribute ``default-date``."""
    _, contributions = _collect_datetime_parts(el, default_date)
    sources = []
    for value_el, raw_part, role in contributions:
        if role == "default-date":
            sources.append(make_source(el, raw_part, attribute="default-date"))
        elif "value-title" in value_el.get("class", []):
            sources.append(make_source(value_el, raw_part, attribute="title"))
        elif value_el.name in ("del", "ins", "time") and value_el.get("datetime"):
            sources.append(make_source(value_el, raw_part, attribute="datetime"))
        elif value_el.name == "data" and value_el.get("value"):
            sources.append(make_source(value_el, raw_part, attribute="value"))
        elif value_el.name == "abbr" and value_el.get("title"):
            sources.append(make_source(value_el, raw_part, attribute="title"))
        else:
            sources.append(make_source(value_el, raw_part))
    return sources
