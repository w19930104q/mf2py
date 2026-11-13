"""functions to parse the properties of elements according to the value class pattern http://microformats.org/wiki/value-class-pattern """

import re

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


def _part_info(value_el):
    """Source info for a single value/value-title element."""
    is_value_title = "value-title" in value_el.get("class", [])
    if is_value_title:
        return {
            "element": value_el,
            "kind": "value-title",
            "attribute": "title",
            "value": value_el.get("title"),
        }
    if value_el.name in ("img", "area"):
        return {
            "element": value_el,
            "kind": "attribute",
            "attribute": "alt",
            "value": value_el.get("alt") or value_el.get_text(),
        }
    if value_el.name == "data":
        return {
            "element": value_el,
            "kind": "attribute",
            "attribute": "value",
            "value": value_el.get("value") or value_el.get_text(),
        }
    if value_el.name == "abbr":
        return {
            "element": value_el,
            "kind": "attribute",
            "attribute": "title",
            "value": value_el.get("title") or value_el.get_text(),
        }
    if value_el.name in ("del", "ins", "time"):
        return {
            "element": value_el,
            "kind": "attribute",
            "attribute": "datetime",
            "value": value_el.get("datetime") or value_el.get_text(),
        }
    return {
        "element": value_el,
        "kind": "text",
        "value": value_el.get_text(),
    }


def text(el, trace=False):
    value_els = _get_vcp_children(el)
    if value_els:
        infos = [_part_info(child) for child in value_els]
        value = "".join(info["value"] or "" for info in infos)
        if trace:
            return value, infos
        return value


def datetime(el, default_date=None, trace=False, default_date_source=None):
    value_els = _get_vcp_children(el)
    if value_els:
        date_parts = []
        infos = []
        for value_el in value_els:
            info = _part_info(value_el)
            infos.append(info)
            if info["value"]:
                date_parts.append(info["value"].strip())

        date_part = time_part = tz_part = None
        date_info = time_info = tz_info = None

        for part, info in zip(date_parts, infos):
            match = re.match(DATETIME_RE + "$", part)
            if match:
                # if it's a full datetime, then we're done
                date_part = match.group("date")
                if trace:
                    info["role"] = "datetime"
                    return normalize_datetime(part, match=match), date_part, infos
                return normalize_datetime(part, match=match), date_part

            # only use first found value
            if re.match(TIME_RE + "$", part) and time_part is None:
                time_part = part
                time_info = info
            elif re.match(DATE_RE + "$", part) and date_part is None:
                date_part = part
                date_info = info
            elif re.match(TIMEZONE_RE + "$", part) and tz_part is None:
                tz_part = part
                tz_info = info

        # use default date
        used_default_date = False
        if date_part is None:
            date_part = default_date
            used_default_date = True

        if date_part and time_part:
            date_time_value = "%s %s" % (date_part, time_part)
        else:
            date_time_value = date_part or time_part

        if tz_part:
            date_time_value += tz_part

        if trace:
            parts = []
            if date_part:
                if used_default_date:
                    parts.append(
                        {
                            "kind": "implied",
                            "role": "date",
                            "value": date_part,
                            "from-default-date": True,
                            "element": default_date_source,
                        }
                    )
                elif date_info is not None:
                    date_info = dict(date_info)
                    date_info["role"] = "date"
                    parts.append(date_info)
            if time_info is not None:
                time_info = dict(time_info)
                time_info["role"] = "time"
                parts.append(time_info)
            if tz_info is not None:
                tz_info = dict(tz_info)
                tz_info["role"] = "timezone"
                parts.append(tz_info)
            return normalize_datetime(date_time_value), date_part, parts
        return normalize_datetime(date_time_value), date_part
