# Provenance / Audit Interface

mf2py can optionally record **provenance** for every value it extracts: where
in the source document each property value came from, which extraction rule
produced it, and the raw text or attribute that contributed to it.

The interface is **opt-in and off by default**. Parsing with it disabled is
unchanged — no audit markers are ever placed inside the business fields of the
normal parse result, and `parse()`, `Parser`, `to_dict()` and `to_json()` keep
their existing output and extraction semantics.

## Enabling auditing

Pass `audit=True` when constructing a `Parser` (or to `parse()`):

```python
import mf2py

parser = mf2py.Parser(doc=html, url="https://example.com/", audit=True)
document = parser.to_dict()          # unchanged, regular mf2json document
entries = parser.audit()             # list of provenance entries
```

Calling `audit()` on a parser built without `audit=True` raises `RuntimeError`.

## Provenance entry shape

Each entry describes **one element of one `properties` array**, including
properties of nested h-* entities and child items:

```json
{
  "pointer": "/items/0/properties/url/0",
  "rule": "u-attr-href",
  "implied": false,
  "sources": [
    {
      "node_path": "html[1] > body[2] > article.h-entry[1] > a.u-url[2]",
      "attribute": "href",
      "value": "/posts/1"
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `pointer` | [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901) JSON Pointer locating the value in the returned document. |
| `rule` | Machine-readable name of the extraction rule (see below). |
| `implied` | `true` when the value was produced by an implied-property rule. |
| `mf1` | Present only for microformats1 backcompat values; records the MF1 mapping, e.g. `{"root": "vcard", "class": "fn"}` or `{"root": "hentry", "rel": "tag"}`. |
| `sources` | One object per contributing raw fragment, in contribution order. |

Each source has:

- `node_path` — a structural CSS-like path to the originating node in the
  **original parsed DOM**. Tag names use 1-based element-only sibling indices
  (`... > a[2]`); classes are appended for readability. In MF1 backcompat mode
  the path resolves back through the parser's rewritten copy (and through
  synthetic elements such as the `<data>` generated for `rel=tag`) to the
  original node.
- `attribute` — optional name of the contributing attribute (`href`, `src`,
  `alt`, `title`, `datetime`, `value`, `html`, ...). Absent for text content.
- `value` — the raw fragment **before** URL normalisation or whitespace
  processing. The normalised value still appears in the parse result; the
  pre-normalisation value remains traceable here.

## Extraction rules

Rules are named after the property type and the winning extraction step:

- `p-text`, `p-attr-title`, `p-attr-value`, `p-attr-alt`,
  `p-value-class-pattern`
- `u-attr-href`, `u-attr-src`, `u-attr-poster`, `u-attr-data`,
  `u-attr-title`, `u-attr-value`, `u-img-src`, `u-text`,
  `u-value-class-pattern`
- `dt-text`, `dt-attr-datetime`, `dt-attr-title`, `dt-attr-value`,
  `dt-value-class-pattern`; a `-default-date` suffix marks a bare time that
  was combined with a date inherited from an earlier `dt-*` property
- `e-embedded` — records both the extracted text and the raw inner HTML
- `implied-name-*`, `implied-photo-*`, `implied-url-*` — implied properties,
  always flagged with `"implied": true`

## Key guarantees

- **One entry per value.** Two different DOM nodes producing identical text
  remain two separate entries with two different `node_path` values; values
  are never merged.
- **Nested entities are covered.** A nested h-* value's own properties get
  pointers such as
  `/items/0/properties/author/0/properties/name/0`.
- **Combined value-class-pattern values list every contributor in order**,
  one source per `.value`/`.value-title` element (for datetimes, the date,
  time and timezone contributions; an inherited default date is included as
  an explicit `default-date` source).
- **Implied properties and MF1 mappings are explicitly marked.**
- **Pointers match the actual returned result.** After filtering by type,
  pointers are relative to the filtered list and resolve against the values
  actually returned.

## Filtering by type

`audit(filter_by_type=...)` mirrors `to_dict(filter_by_type=...)`. Pointers
are relative to the filtered list, so they still point at the returned values:

```python
cards = parser.to_dict(filter_by_type="h-card")
entries = parser.audit(filter_by_type="h-card")
# entries' pointers start at "/0/...", resolving inside `cards`
```

## JSON output

`to_json()` output is unchanged by default. Pass `include_audit=True` to wrap
the document together with its provenance:

```python
parser.to_json(include_audit=True)
# {"document": { ...regular mf2json... }, "audit": [ ...provenance... ]}
```

## Scope and limitations

- Provenance covers h-* items produced from MF2 and MF1 markup. Metaformats
  items and byte offsets into the source text are intentionally out of scope.
- `rels`/`rel-urls` are not part of the item `properties` arrays and have no
  provenance entries.
