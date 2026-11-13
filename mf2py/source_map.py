"""Field provenance ("source map") support.

When the parser is constructed with ``source_map=True`` every value that ends
up in the parsed mf2json output gets a *trace* describing where in the original
DOM (and via which parsing rule) it came from.

The traces are kept completely separate from the parsed values: they are
collected here and only attached to the returned document by
:meth:`mf2py.parser.Parser.to_dict` / :meth:`mf2py.parser.Parser.to_json` /
:meth:`mf2py.parser.Parser.source_map`.  When the feature is disabled no trace
is ever created and the parser output is byte-for-byte the same as before.

A trace is a plain JSON-serialisable dictionary.  Every trace contains:

* ``kind`` -- one of ``"attribute"``, ``"text"``,
  ``"value-class-pattern"``, ``"value-title"``, ``"embedded"``,
  ``"implied"``, ``"backcompat"``, ``"composite-datetime"`` or
  ``"metaformats"``
* ``paths`` -- DOM paths (XPath-like positional paths, e.g.
  ``/html/body/article[1]/span[1]``) of the elements that contributed the
  value.  There can be more than one path (composite values); values are
  never merged by reverse-looking-up their text, so duplicate values each
  keep their own trace.

Optional keys:

* ``attribute`` -- name of the source attribute (``"href"``, ``"alt"`` ...)
* ``value`` -- the raw value as authored in the DOM *before* processing
  (URL resolution, datetime normalisation, whitespace collapsing ...)
* ``base-url`` -- effective base URL used while resolving relative URLs
* ``parts`` -- contributing pieces of a value-class-pattern / composite
  datetime, each a trace dictionary with a ``role`` of ``"date"``,
  ``"time"``, ``"timezone"`` or ``"datetime"``
* ``from-default-date`` -- the piece was filled in from an earlier dt-*
  property (the legacy implied-date rule)
* ``mapping`` / ``backcompat-root`` -- the microformats1 -> microformats2
  class mapping that was applied
"""

from .dom_helpers import get_children


def _original(el):
    """Return the element this (possibly backcompat-copied) element mirrors.

    bs4's :class:`~bs4.element.Tag` implements ``__getattr__`` as a
    ``find()`` shortcut, so ``el.original`` silently returns ``None`` when the
    attribute has not been set instead of raising :class:`AttributeError`.
    Always check the instance dictionary explicitly.
    """
    if "original" in el.__dict__:
        return el.__dict__["original"]
    return None


def dom_path(el):
    """Build an XPath-like positional path for ``el`` in the original DOM.

    The path uses 1-based positions among same-named siblings, e.g.
    ``/html/body/article[2]/p[1]``.  Elements that only exist inside a
    backcompat working copy are resolved through their ``original`` link so
    the path always points at the document the caller handed to the parser.
    """
    original = _original(el)
    if original is not None and original is not el:
        return dom_path(original)

    chunks = []
    node = el
    while node is not None and getattr(node, "name", None) is not None:
        # the BeautifulSoup document root has the pseudo-name "[document]";
        # it is not an HTML element and must not appear in the path
        if node.name == "[document]":
            break
        parent = node.parent
        if parent is None:
            # detached element: either a tag synthesised inside a backcompat
            # working copy or the root of such a copy. Resolve through the
            # "original" link when available, otherwise emit the tag name
            # without a position as a best effort.
            mirrored = _original(node)
            if mirrored is not None and mirrored is not node:
                suffix = "/".join(chunks)
                mirrored_path = dom_path(mirrored)
                return mirrored_path + ("/" + suffix if suffix else "")
            chunks.insert(0, node.name)
            break
        same_named = [
            child for child in get_children(parent) if child.name == node.name
        ]
        # use identity comparison: two distinct (but structurally equal) tags
        # would collapse under list.index's == comparison
        position = next(
            (i + 1 for i, child in enumerate(same_named) if child is node), 1
        )
        chunks.insert(0, "%s[%d]" % (node.name, position))
        node = parent
    return "/" + "/".join(chunks)


def paths_of(*elements):
    """DOM paths of the given elements, de-duplicated, preserving order."""
    seen = []
    for el in elements:
        if el is None:
            continue
        path = dom_path(el)
        if path not in seen:
            seen.append(path)
    return seen


def _escape_pointer(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class SourceMap:
    """Collects traces while parsing and renders the final source-map dict.

    An owner stack mirrors the nesting of h-* microformats: every trace
    produced while a microformat is being parsed is attached to that
    microformat's node.  Traces are keyed by the microformat dictionary
    identity, never by value, so repeated or identical values are not merged.
    """

    def __init__(self):
        # id(microformat dict) -> node
        self.nodes = {}
        # stack of microformat dictionaries currently being parsed
        self.stack = []
        # property name -> trace info, populated by the metaformats parser
        self.metaformats_traces = {}

    # -- trace construction -------------------------------------------------

    @staticmethod
    def trace(kind, elements, **extra):
        record = {"kind": kind, "paths": paths_of(*_as_list(elements))}
        record.update(extra)
        return record

    def text_trace(self, el, info):
        """Build a trace from the info returned by the property parsers."""
        if info is None:
            return None
        # value-class-pattern for p-* / u-* returns the raw piece list
        if isinstance(info, list):
            parts = [self._part_trace(part) for part in info]
            kind = (
                "value-title"
                if any(part.get("kind") == "value-title" for part in info)
                else "value-class-pattern"
            )
            return self.trace(
                kind,
                [part.get("element") for part in info if part.get("element")] or [el],
                parts=parts,
                value="".join(str(part.get("value") or "") for part in info),
            )
        if info.get("parts"):
            parts = [self._part_trace(part) for part in info["parts"]]
            kind = (
                "composite-datetime"
                if info.get("kind") != "value-class-pattern"
                else "value-class-pattern"
            )
            record = self.trace(
                kind,
                [part.get("element") for part in info["parts"] if part.get("element")]
                or [el],
                parts=parts,
            )
            if info.get("value") is not None:
                record["value"] = info["value"]
            if info.get("base-url") is not None:
                record["base-url"] = info["base-url"]
            if info.get("from-default-date"):
                record["from-default-date"] = True
            return record

        source_el = info.get("element", el)
        record = self.trace(info.get("kind", "text"), [source_el])
        if info.get("attribute"):
            record["attribute"] = info["attribute"]
        if "value" in info:
            record["value"] = info["value"]
        if info.get("base-url") is not None:
            record["base-url"] = info["base-url"]
        return record

    def _part_trace(self, part):
        record = {}
        if "value" in part:
            record["value"] = part["value"]
        if part.get("element") is not None:
            record["paths"] = paths_of(part["element"])
        if part.get("attribute"):
            record["attribute"] = part["attribute"]
        if part.get("kind"):
            record["kind"] = part["kind"]
        if part.get("role"):
            record["role"] = part["role"]
        if part.get("from-default-date"):
            record["from-default-date"] = True
        if part.get("base-url") is not None:
            record["base-url"] = part["base-url"]
        return record

    # -- node bookkeeping ---------------------------------------------------

    def register_node(self, microformat, root_trace=None):
        node = {
            "root": root_trace,
            "properties": {},
            "value": None,
        }
        self.nodes[id(microformat)] = node
        self.stack.append(microformat)
        return node

    def pop_node(self, microformat):
        if self.stack and self.stack[-1] is microformat:
            self.stack.pop()

    def get_node(self, microformat):
        return self.nodes.get(id(microformat))

    def attach_property(self, prop_name, trace):
        if not self.stack:
            return
        node = self.nodes[id(self.stack[-1])]
        node["properties"].setdefault(prop_name, []).append(trace)

    def attach_value(self, trace):
        if not self.stack:
            return
        self.nodes[id(self.stack[-1])]["value"] = trace

    # -- rendering ----------------------------------------------------------

    def build(self, items, prefix="/items"):
        """Render the source-map dictionary for the given items list.

        The map is built by walking the actual output structure, so JSON
        Pointers always address the returned result -- including a filtered
        list passed in by :meth:`mf2py.parser.Parser.source_map`.

        Args:
          items (list): microformat items being addressed
          prefix (str): JSON Pointer prefix of the items list. Defaults to
            ``"/items"``; pass ``""`` to address a bare filtered list.
        """
        mapping = {}
        for index, item in enumerate(items):
            self._render_item(item, "%s/%d" % (prefix, index), mapping)
        return mapping

    def _render_item(self, item, pointer, mapping):
        node = self.get_node(item)
        if node is None:
            return
        if node["root"] is not None:
            mapping[pointer] = [node["root"]]
        if node["value"] is not None and "value" in item:
            mapping[pointer + "/value"] = [node["value"]]

        props_pointer = pointer + "/properties"
        for prop_name, values in item.get("properties", {}).items():
            traces = node["properties"].get(prop_name, [])
            prop_pointer = props_pointer + "/" + _escape_pointer(prop_name)
            for index, value in enumerate(values):
                member_pointer = "%s/%d" % (prop_pointer, index)
                if index < len(traces):
                    mapping[member_pointer] = [traces[index]]
                # nested h-* microformat member: render its own subtree at
                # the same pointer; it is reached structurally, never by
                # looking values up
                if isinstance(value, dict) and self.get_node(value) is not None:
                    self._render_item(value, member_pointer, mapping)

        for index, child in enumerate(item.get("children", [])):
            child_pointer = pointer + "/children/%d" % index
            child_node = self.get_node(child)
            if child_node is not None and child_node["root"] is not None:
                mapping[child_pointer] = [child_node["root"]]
            self._render_item(child, child_pointer, mapping)
