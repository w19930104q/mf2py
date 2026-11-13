"""Implementation of the classic microformats `include-pattern`.

https://microformats.org/wiki/include-pattern

Pages that carry many microformat records (e.g. an event listing) often keep
shared data such as venue information in one fragment and reference it from
every record with

    <a class="include" href="#venue"></a>

or

    <object class="include" data="#venue"></object>

When include mode is enabled (it is off by default), each such reference
element is replaced by a copy of the referenced subtree *before*
microformats1/microformats2 extraction runs, so the referenced content is
parsed exactly as if it had been authored in place: nested h-* scoping, the
value-class-pattern and implied properties all apply to the expanded tree.

Semantics:

* Only same-page fragment references (``#id``) are supported. References to
  other pages are never fetched and are reported as diagnostics instead.
* The reference element is replaced wholesale, so its own class names (other
  than marking it as an include) never take part in parsing.
* Forward, backward, multi-level and shared references all work; every
  reference is expanded at its own position and repeated references keep
  their duplicate values.
* References that cannot be expanded - self references, references to an
  ancestor, indirect reference loops, missing or duplicate ids, missing or
  external references - are left in place unexpanded and produce a
  machine-readable diagnostic; parsing of the rest of the document continues
  normally.
* Includes located inside an e-* property element (or a classic microformat
  property that backcompat upgrades to e-*) are not expanded, so e-*
  properties keep outputting the originally authored HTML, subject to the
  usual cleanup and URL resolution rules.
* Expansion happens on an internal copy of the document; the caller's
  document is never modified, and rel/rel-urls collection still runs on the
  original document.
* The total number of DOM nodes inserted by expansion is capped (see
  ``DEFAULT_MAX_NODES``). Exceeding the cap raises ``IncludeLimitExceeded``
  so that no silently truncated data is ever delivered.

Diagnostics are dicts with the keys:

* ``reason``: one of ``missing-reference``, ``external-reference``,
  ``missing-id``, ``duplicate-id``, ``self-reference``,
  ``ancestor-reference`` or ``circular-reference``
* ``reference``: the href/data value of the include element (or None)
* ``target``: the referenced fragment id, if one was present
* ``element``: the serialized include element
* ``sourceline``/``sourcepos``: position in the source, when the underlying
  HTML parser provides it
"""

import copy

from . import backcompat, mf2_classes
from .dom_helpers import get_descendents

#: Default cap on the total number of DOM nodes inserted by include
#: expansion during a single parse.
DEFAULT_MAX_NODES = 10000


class IncludeError(Exception):
    """Base class for include-pattern errors."""


class IncludeLimitExceeded(IncludeError):
    """Raised when include expansion exceeds the configured node limit.

    No parse result is delivered in this case, so callers never see
    silently truncated data.
    """


def _classic_embedded_classes():
    """mf1 class names that backcompat rules upgrade to e-* properties"""
    classes = set()
    for rules in backcompat._CLASSIC_MAP.values():
        for old_classes, new_classes in rules.get("properties", {}).items():
            if mf2_classes.has_embedded_class(new_classes):
                classes.update(old_classes.split())
    return classes


_CLASSIC_EMBEDDED_CLASSES = _classic_embedded_classes()


def _is_include(el):
    """True if el is an include-pattern reference element (a.include or object.include)"""
    return el.name in ("a", "object") and "include" in el.get("class", [])


def _reference_of(el):
    """The reference (href or data attribute) of an include element, or None"""
    if el.name == "a":
        return el.get("href")
    return el.get("data")


def _in_embedded_context(el):
    """True if el sits inside an e-* property element, i.e. an element with an
    authored mf2 e-* class or a classic mf1 class that backcompat maps to e-*.
    Includes in such subtrees are not expanded so that e-* properties keep
    outputting the originally authored HTML."""
    for parent in el.parents:
        classes = parent.get("class", [])
        if mf2_classes.has_embedded_class(
            classes
        ) or not _CLASSIC_EMBEDDED_CLASSES.isdisjoint(classes):
            return True
    return False


def _is_attached(el, root):
    """True if el is still part of the tree rooted at root. Include elements
    nested in the fallback content of a replaced <object> include become
    detached and must be skipped."""
    top = el
    for top in el.parents:
        pass
    return top is root


def _summarize(el, limit=500):
    """Serialized form of an element for diagnostics, truncated if huge"""
    text = str(el)
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


def _make_diagnostic(el, reason, reference, target_id):
    diagnostic = {
        "reason": reason,
        "reference": reference,
        "target": target_id,
        "element": _summarize(el),
    }
    if el.sourceline is not None:
        diagnostic["sourceline"] = el.sourceline
        diagnostic["sourcepos"] = el.sourcepos
    return diagnostic


def _index_ids(root):
    """Map of id attribute values to elements, plus the set of duplicated ids"""
    ids = {}
    duplicates = set()
    for el in get_descendents(root):
        id_ = el.get("id")
        if not id_:
            continue
        if id_ in ids:
            duplicates.add(id_)
        else:
            ids[id_] = el
    return ids, duplicates


def expand(doc, max_nodes=DEFAULT_MAX_NODES, diagnostics=None):
    """Expand include-pattern references in a copy of doc and return the copy.

    The given document is never modified. Expansion failures are collected
    as dicts in diagnostics and parsing can continue with the rest of the
    document.

    Args:
      doc (BeautifulSoup or Tag): the parsed document
      max_nodes (int or None): cap on the total number of DOM nodes inserted
        by expansion. None disables the cap (not recommended: pathological
        documents can expand exponentially).
      diagnostics (list): list that expansion diagnostics are appended to.

    Returns:
      the expanded copy of doc

    Raises:
      IncludeLimitExceeded: if the node cap is exceeded. No (possibly
        truncated) tree is returned in that case.
    """
    if diagnostics is None:
        diagnostics = []

    working = copy.copy(doc)

    # targets are always cloned from the pristine source tree, so expansion
    # results do not depend on processing order and reference loops terminate
    source_ids, duplicate_ids = _index_ids(doc)
    # positions (self/ancestor checks) are evaluated against the working tree
    working_ids, _ = _index_ids(working)

    # stack of (include element, chain of target ids expanded around it);
    # entries are pushed in reverse document order so they pop in document order
    stack = [
        (el, ())
        for el in reversed(list(get_descendents(working)))
        if _is_include(el) and not _in_embedded_context(el)
    ]

    expanded_nodes = 0

    while stack:
        el, chain = stack.pop()

        if not _is_attached(el, working):
            # fell out of the tree when an enclosing include was replaced
            continue

        reference = _reference_of(el)
        reason = None
        target_id = None

        ref = reference.strip() if isinstance(reference, str) else ""
        if not ref:
            reason = "missing-reference"
        elif not ref.startswith("#"):
            # external page: never fetched
            reason = "external-reference"
        else:
            target_id = ref[1:]
            if not target_id:
                reason = "missing-reference"
            elif target_id in duplicate_ids:
                reason = "duplicate-id"
            elif target_id not in source_ids:
                reason = "missing-id"
            else:
                target = working_ids[target_id]
                if target is el:
                    reason = "self-reference"
                elif any(parent is target for parent in el.parents):
                    reason = "ancestor-reference"
                elif target_id in chain:
                    reason = "circular-reference"

        if reason is not None:
            diagnostics.append(_make_diagnostic(el, reason, reference, target_id))
            continue

        clone = copy.copy(source_ids[target_id])
        expanded_nodes += sum(1 for _ in clone.descendants) + 1
        if max_nodes is not None and expanded_nodes > max_nodes:
            raise IncludeLimitExceeded(
                "include expansion exceeded the limit of {0} nodes "
                "while expanding reference {1!r}".format(max_nodes, reference)
            )

        el.replace_with(clone)

        chain = chain + (target_id,)
        candidates = [clone] + list(get_descendents(clone))
        stack.extend(
            (candidate, chain)
            for candidate in reversed(candidates)
            if _is_include(candidate) and not _in_embedded_context(candidate)
        )

    return working
