"""Provenance / audit trail support for parsed microformats.

This module is opt-in (``audit=True`` on :class:`mf2py.Parser`) and never
mutates the regular parse result. It records, for every element of every
``properties`` array (including properties of nested h-* entities):

* the :rfc:`6901` JSON Pointer of the value inside the returned document,
* a structural node path locating the originating element in the *original*
  parsed DOM (back to the original element even in MF1 backcompat mode, where
  the parser works on a rewritten copy),
* the extraction rule that produced the value, and
* every raw text/attribute fragment that contributed to the value, captured
  *before* URL normalisation or whitespace processing.

Combined value-class-pattern values list one source per contributing element
in contribution order; implied properties and MF1 -> MF2 mappings are marked
explicitly. Two different DOM nodes that happen to produce identical text
remain two separate entries with two separate node paths.
"""

from bs4.element import Tag


# ---------------------------------------------------------------------------
# node paths in the original DOM
# ---------------------------------------------------------------------------


def node_path(el):
    """Return a structural CSS-like path for *el* inside its document.

    The path is built from tag names and 1-based element-only sibling
    positions, e.g. ``html > body > article.h-entry[1] > div[2] > a[1]``.
    Class names are appended (when present) purely for readability; the
    sibling index makes the path unique even for duplicate markup.
    """
    parts = []
    node = el
    while isinstance(node, Tag) and node.parent is not None:
        index = 0
        for sibling in node.parent.children:
            if isinstance(sibling, Tag) and sibling.name != "template":
                index += 1
                if sibling is node:
                    break
        label = node.name or ""
        classes = node.get("class") or []
        if classes:
            label += "." + ".".join(classes)
        label += "[%d]" % index
        parts.append(label)
        node = node.parent
    parts.reverse()
    return " > ".join(parts)


def build_node_path_map(root):
    """Map every DOM tag reachable from *root* to its :func:`node_path`."""
    paths = {}
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, Tag):
            paths[id(node)] = node_path(node)
            stack.extend(reversed(list(node.children)))
    return paths


def resolve_node_path(el, path_map, registry=None):
    """Resolve *el* (which may belong to a backcompat DOM copy or be a
    synthetic element) back to a path in the original DOM.

    Backcompat processing records copied/synthetic elements in an external
    registry (bs4 deep copies do not preserve arbitrary Tag attributes); the
    chain is followed until a node present in the original DOM is reached.
    """
    from . import backcompat

    seen = set()
    node = el
    while node is not None and id(node) not in seen:
        if id(node) in path_map:
            return path_map[id(node)]
        seen.add(id(node))
        node = (
            backcompat.audit_original(registry, node) if registry is not None else None
        )
    # fall back (should not happen for elements reachable from the document)
    return node_path(el)


# ---------------------------------------------------------------------------
# JSON Pointer helpers (RFC 6901)
# ---------------------------------------------------------------------------


def _escape_pointer_token(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer(tokens):
    """Build a JSON Pointer from a list of unescaped reference tokens."""
    if not tokens:
        return ""
    return "/" + "/".join(_escape_pointer_token(t) for t in tokens)


# ---------------------------------------------------------------------------
# provenance leaves and the parallel ("shadow") tree
# ---------------------------------------------------------------------------


def make_source(node, value, attribute=None):
    """Build one raw contribution record.

    Args:
      node (bs4 Tag): the DOM element the fragment came from.
      value (str|None): the raw fragment text/attribute value, captured
        before normalisation.
      attribute (str|None): name of the contributing attribute, or None for
        text content.
    """
    src = {"node": node, "value": value}
    if attribute is not None:
        src["attribute"] = attribute
    return src


def make_leaf(rule, sources, implied=False, mf1=None):
    """Provenance for a single (non-entity) properties-array element."""
    leaf = {
        "rule": rule,
        "sources": list(sources),
        "implied": bool(implied),
    }
    if mf1 is not None:
        leaf["mf1"] = mf1
    return leaf


class ShadowEntity:
    """Provenance counterpart of a parsed h-* item dict.

    ``properties`` maps each property name to a list aligned 1:1 with the
    parsed item's ``properties[name]`` array; each element is either a
    :class:`ShadowEntity` (nested h-* value) or a provenance leaf dict.
    ``children`` is aligned 1:1 with the parsed item's ``children`` array.
    """

    def __init__(self, node, mf1=None):
        self.node = node  # root element (possibly a backcompat copy)
        self.mf1 = mf1  # {"root": "vcard"} or None
        self.properties = {}
        self.children = []

    def add_property(self, name, shadow_value):
        self.properties.setdefault(name, []).append(shadow_value)


def _is_entity(value):
    return isinstance(value, dict) and "type" in value and "properties" in value


# ---------------------------------------------------------------------------
# parallel walk: shadow tree + parsed tree -> flat provenance entries
# ---------------------------------------------------------------------------


def _serialize_sources(sources, path_map, registry):
    out = []
    for src in sources:
        entry = {}
        node = src.get("node")
        if node is not None:
            entry["node_path"] = resolve_node_path(node, path_map, registry)
        if src.get("attribute"):
            entry["attribute"] = src["attribute"]
        entry["value"] = src.get("value")
        out.append(entry)
    return out


def _walk_entity(shadow, parsed_entity, tokens, path_map, out, registry):
    tokens = list(tokens)
    properties = parsed_entity.get("properties", {})
    for prop_name, values in properties.items():
        leaf_shadows = shadow.properties.get(prop_name, [])
        for index, value in enumerate(values):
            value_tokens = tokens + ["properties", prop_name, index]
            if index >= len(leaf_shadows):
                continue
            leaf = leaf_shadows[index]
            if isinstance(leaf, ShadowEntity):
                if _is_entity(value):
                    _walk_entity(leaf, value, value_tokens, path_map, out, registry)
            else:
                entry = {
                    "pointer": pointer(value_tokens),
                    "rule": leaf["rule"],
                    "implied": leaf["implied"],
                    "sources": _serialize_sources(leaf["sources"], path_map, registry),
                }
                if leaf.get("mf1") is not None:
                    entry["mf1"] = leaf["mf1"]
                out.append(entry)
    for index, child in enumerate(parsed_entity.get("children", [])):
        if index >= len(shadow.children):
            continue
        child_shadow = shadow.children[index]
        if isinstance(child_shadow, ShadowEntity) and _is_entity(child):
            _walk_entity(
                child_shadow,
                child,
                tokens + ["children", index],
                path_map,
                out,
                registry,
            )


def audit_entries(top_shadows, items, path_map, pointer_prefix=(), registry=None):
    """Build serialisable provenance entries.

    Args:
      top_shadows (list): top-level shadows aligned with *items*
        (metaformats items have no shadow and are skipped).
      items (list[dict]): the parsed items actually being returned (already
        filtered when ``filter_by_type`` is used).
      path_map (dict): ``id(tag) -> node path`` for the original DOM.
      pointer_prefix (tuple): JSON Pointer tokens of the audited root; empty
        for the full document, ``(i,)`` when auditing a filtered list whose
        element indices must still match the returned result.
      registry (dict|None): backcompat provenance registry.

    Returns:
      list[dict] sorted by JSON Pointer.
    """
    out = []
    for index, item in enumerate(items):
        if index >= len(top_shadows):
            continue
        shadow = top_shadows[index]
        if not isinstance(shadow, ShadowEntity) or not _is_entity(item):
            continue
        _walk_entity(
            shadow,
            item,
            tuple(pointer_prefix) + (index,),
            path_map,
            out,
            registry,
        )
    out.sort(key=lambda e: e["pointer"])
    return out
