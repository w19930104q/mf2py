# Field Provenance (`source_map`)

When the same page produces several authors or dates with identical text, the
mf2json result alone cannot tell you *which* DOM element each value came from.
Pass `source_map=True` to `mf2py.parse()` or `Parser(...)` to collect a
provenance map alongside the ordinary output. The feature is **off by
default**; when it is off nothing changes in the parsed document.

```python
import mf2py

with open("test/examples/source_map_regression.html") as fp:
    parser = mf2py.Parser(
        doc=fp,
        html_parser="html5lib",
        source_map=True,
        url="http://example.com/",
    )

document = parser.to_dict()
document["source-map"]
```

## Where the map lives

* `Parser.to_dict()` returns the usual document with an extra top-level
  `"source-map"` key (the other keys, values and ordering are untouched).
* `Parser.source_map()` returns just the map.
* `Parser.to_json()` serialises the document including `"source-map"`.
* With a type filter, `to_dict(filter_by_type=...)` still returns a bare list.
  Call `source_map(filter_by_type=...)` for the matching map; its JSON
  Pointers are rebased onto that list (e.g. `/0/properties/name/0`).

The input BeautifulSoup object is never modified by tracing. Back-compat
(classic microformats) rewriting keeps working on an internal working copy.

## JSON Pointers

Each key is an [RFC 6901](https://datatracker.ietf.org/doc/html/rfc6901)
JSON Pointer addressing a member of the returned result; each value is a
one-element list of **trace** dictionaries. Nested `h-*` members are reached
structurally through their own pointer, never by reverse-looking-up a value,
so duplicate or structurally identical elements keep independent traces.

```text
/items/0/properties/author/0/properties/name/0
```

## DOM paths

A trace's `paths` are XPath-like positional paths into the original document,
using 1-based positions among same-named siblings, e.g.
`/html/body/article[1]/span[2]`. Elements created inside a back-compat
working copy (including the synthesised `<data>` for `rel=tag`) resolve
through their authored element, so paths always address the document you
handed to the parser.

## Trace kinds

| `kind`                 | meaning                                                            |
| ---------------------- | ------------------------------------------------------------------ |
| `attribute`            | value taken from an element attribute (`href`, `alt`, `title`...)  |
| `text`                 | value taken from rendered text content                             |
| `value-class-pattern`  | value assembled from `value` / `value-title` child elements        |
| `value-title`          | value taken from a `value-title` element's `title` attribute       |
| `embedded`             | `e-*` property (text value plus HTML)                              |
| `implied`              | implied name / photo / url property                                |
| `backcompat`           | produced through a microformats1 → microformats2 mapping           |
| `composite-datetime`   | datetime assembled from separate date and time sources             |
| `metaformats`          | value taken from a `<meta>` element                                |

Every trace has `kind` and `paths`. It may additionally carry:

* `attribute` — the source attribute name
* `value` — the raw, pre-processing value (before URL resolution, datetime
  normalisation or whitespace collapsing)
* `base-url` — the effective base URL used to resolve a relative URL
* `parts` — the contributing pieces of a value-class-pattern or composite
  datetime, each tagged with a `role` of `date`, `time`, `timezone` or
  `datetime`
* `from-default-date` — the piece was filled in from an earlier `dt-*`
  property under the legacy implied-date rule
* `mapping` / `backcompat-root` — the classic class/rel mapping that was
  applied

## Examples

### URL: pre-resolution value and base URL

```json
{
  "kind": "attribute",
  "paths": ["/html/body/article/a[1]"],
  "attribute": "href",
  "value": "/posts/1",
  "base-url": "http://example.com/"
}
```

The emitted property value is the resolved `http://example.com/posts/1`,
while the trace keeps both the authored `href` and the base that resolved it.

### Composite datetime

A date and a time authored in separate `value` elements:

```json
{
  "kind": "value-class-pattern",
  "paths": [".../span/data[1]", ".../span/data[2]", ".../span/span[1]"],
  "value": "2024-05-21 18:30-0600",
  "parts": [
    {"role": "date", "value": "2024-05-21", "paths": [".../span/data[1]"],
     "kind": "attribute", "attribute": "value"},
    {"role": "time", "value": "18:30", "paths": [".../span/data[2]"],
     "kind": "attribute", "attribute": "value"},
    {"role": "timezone", "value": "-06:00", "paths": [".../span/span[1]"],
     "kind": "text"}
  ]
}
```

When a time-only property borrows the date from a previous one, the date
piece is marked `"from-default-date": true` and points at the earlier
datetime element.

### Repeated values

Two elements that happen to contain the same text each get their own trace:

```json
"/items/0/properties/category/0": [{"paths": [".../article/span[3]"], ...}],
"/items/0/properties/category/1": [{"paths": [".../article/span[4]"], ...}]
```

### Back-compat mapping

```json
{
  "kind": "backcompat",
  "paths": ["/html/body/div/span[1]"],
  "value": "Legacy Card",
  "mapping": {"classes": ["fn"], "properties": ["p-name"]}
}
```

The microformat root trace additionally records the root mapping, e.g.
`"backcompat-root": ["vcard"]` with `"mapping": {"vcard": ["h-card"]}`.
