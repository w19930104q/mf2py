import copy
import json
from urllib.parse import urlparse

from bs4 import BeautifulSoup, FeatureNotFound
from bs4.element import Tag

from . import (
    backcompat,
    implied_properties,
    metaformats,
    mf2_classes,
    parse_property,
    temp_fixes,
)
from .audit import (
    ShadowEntity,
    audit_entries,
    build_node_path_map,
)
from .dom_helpers import get_attr, get_children, get_descendents, try_urljoin
from .mf_helpers import unordered_list
from .version import __version__


def parse(
    doc=None,
    url=None,
    html_parser=None,
    http_client=None,
    expose_dom=False,
    filter_roots=False,
    metaformats=False,
    audit=False,
):
    """
    Parse a document or URL for microformats and return a dictionary in mf2json format.

    Args:
      doc (file, string or BeautifulSoup doc): file handle, text of content
        to parse, or BeautifulSoup document. If None it will be fetched from
        given URL.
      url (string): URL of the file to be processed. If None it will be
        extracted from the `<base>` element of given doc.
      html_parser (string): optional, select a specific HTML parser. Valid options
        from the BeautifulSoup documentation are: "html", "xml","html5", "lxml",
        "html5lib", and "html.parser".
      http_client (module or object): optional, supply an external HTTP client
        providing a `.get()` function or method. Defaults to `requests`.
      expose_dom (boolean): optional, expose the DOM of embedded properties.
      metaformats (boolean): optional, include metaformats extracted from OGP
        and Twitter card data: https://microformats.org/wiki/metaformats
      filter_roots (boolean or list): optional, filter root class names. Use
        True to filter known conflicting classes, otherwise filter given list.
      audit (boolean): optional, collect provenance for every properties-array
        element. Defaults to False; when False parsing behaves exactly as
        before. Access the audit trail via :meth:`Parser.audit`.

    Return: a mf2json dict representing the structured data in the document

    """
    return Parser(
        doc,
        url,
        html_parser,
        http_client=http_client,
        expose_dom=expose_dom,
        metaformats=metaformats,
        filter_roots=filter_roots,
        audit=audit,
    ).to_dict()


class Parser(object):
    """
    Parser to parse a document or URL for microformats and output in various formats.

    Args:
      doc (file, string or BeautifulSoup doc): file handle, text of content
        to parse, or BeautifulSoup document. If None it will be fetched from
        given URL.
      url (string): URL of the file to be processed. If None it will be
        extracted from the `<base>` element of given doc.
      html_parser (string): optional, select a specific HTML parser. Valid options
        from the BeautifulSoup documentation are: "html", "xml","html5", "lxml",
        "html5lib", and "html.parser".
      http_client (module or object): optional, supply an external HTTP client
        providing a `.get()` function or method. Defaults to `requests`.
      expose_dom (boolean): optional, expose the DOM of embedded properties.
      metaformats (boolean): optional, include metaformats extracted from OGP
        and Twitter card data: https://microformats.org/wiki/metaformats
      filter_roots (boolean or list): optional, filter root class names. Use
        True to filter known conflicting classes, otherwise filter given list.
      audit (boolean): optional, collect provenance for every properties-array
        element. Off by default. See :meth:`audit`.

    Attributes:
      useragent (string): the User-Agent string for the Parser

    """

    ua_desc = "mf2py - microformats2 parser for python"
    ua_url = "https://github.com/microformats/mf2py"
    useragent = "{0} - version {1} - {2}".format(ua_desc, __version__, ua_url)

    def __init__(
        self,
        doc=None,
        url=None,
        html_parser=None,
        http_client=None,
        expose_dom=False,
        metaformats=False,
        filter_roots=False,
        audit=False,
    ):
        self.__url__ = None
        self.__doc__ = None
        self._preserve_doc = False
        self.audit_enabled = bool(audit)
        # parallel shadow tree: ShadowEntity objects aligned with parsed items
        self._audit_shadows = []
        # id(tag) -> node path, built from the original DOM on demand
        self._audit_path_map = None
        # backcompat provenance registry (originals + MF1 mappings), reused
        # for every backcompat root in this document
        self._audit_registry = backcompat._new_audit_registry() if audit else None
        self.__parsed__ = {
            "items": [],
            "rels": {},
            "rel-urls": {},
            "debug": {
                "description": self.ua_desc,
                "source": self.ua_url,
                "version": __version__,
            },
        }
        self.lang = None
        self.expose_dom = expose_dom
        self.__metaformats = metaformats
        try:
            self.filtered_roots = set(filter_roots)
        except TypeError:
            if filter_roots:
                self.filtered_roots = mf2_classes.CONFLICTING_ROOTS_TAILWIND
            else:
                self.filtered_roots = []

        # use default parser if none specified
        self.__html_parser__ = html_parser or "html5lib"

        if url is not None:
            self.__url__ = url

            if doc is None:
                if http_client is None:
                    import requests

                    http_client = requests
                data = http_client.get(
                    self.__url__,
                    headers={
                        "User-Agent": self.useragent,
                    },
                )

                # update to final URL after redirects
                self.__url__ = str(data.url)

                # HACK: check for character encodings and use 'correct' data
                if "charset" in data.headers.get("content-type", ""):
                    doc = data.text
                else:
                    doc = data.content

        if doc is not None:
            if isinstance(doc, BeautifulSoup) or isinstance(doc, Tag):
                self.__doc__ = doc
                self._preserve_doc = True
            else:
                try:
                    # try the user-given html parser or default html5lib
                    self.__doc__ = BeautifulSoup(doc, features=self.__html_parser__)
                except FeatureNotFound:
                    # maybe raise a warning?
                    # else switch to default use
                    self.__doc__ = BeautifulSoup(doc)

        # update actual parser used
        # uses builder.NAME from BeautifulSoup
        if isinstance(self.__doc__, BeautifulSoup) and self.__doc__.builder is not None:
            self.__html_parser__ = self.__doc__.builder.NAME
        else:
            self.__html_parser__ = None

        # check for <base> tag
        if self.__doc__:
            poss_base = next(
                (el for el in get_descendents(self.__doc__) if el.name == "base"), None
            )
            if poss_base:
                poss_base_url = poss_base.get("href")  # try to get href
                if poss_base_url:
                    if urlparse(poss_base_url).netloc:
                        # base specifies an absolute path
                        self.__url__ = poss_base_url
                    elif self.__url__:
                        # base specifies a relative path
                        self.__url__ = try_urljoin(self.__url__, poss_base_url)

        if self.__doc__ is not None:
            if document := self.__doc__.find("html"):
                self.lang = document.attrs.get("lang")
            # parse!
            self._parse()

    def _parse(self):
        """Does the work of actually parsing the document. Done automatically
        on initialization.
        """
        self._default_date = None
        # _default_date exists to provide implementation for rules described
        # in legacy value-class-pattern. basically, if you have two dt-
        # properties and one does not have the full date, it can use the
        # existing date as a template.
        # see value-class-pattern#microformats2_parsers on wiki.
        # see also the implied_relative_datetimes testcase.

        def handle_microformat(
            root_class_names,
            el,
            value_property=None,
            simple_value=None,
            backcompat_mode=False,
            original_el=None,
        ):
            """Handles a (possibly nested) microformat, i.e. h-*"""
            properties = {}
            children = []
            self._default_date = None
            # for processing implied properties: collects if property types (p, e, u, d(t)) or children (h) have been processed
            parsed_types_aggregation = set()

            # provenance counterpart of this item; None unless auditing
            shadow = (
                ShadowEntity(
                    original_el if original_el is not None else el,
                    mf1=(
                        {"root": " ".join(root_class_names)}
                        if backcompat_mode
                        else None
                    ),
                )
                if self.audit_enabled
                else None
            )

            if backcompat_mode:
                el = backcompat.apply_rules(
                    el,
                    self.__html_parser__,
                    self.filtered_roots,
                    audit=self.audit_enabled,
                    registry=self._audit_registry,
                )
                root_class_names = mf2_classes.root(
                    el.get("class", []), self.filtered_roots
                )

            root_lang = el.attrs.get("lang")

            # parse for properties and children
            for child in get_children(el):
                (
                    child_props,
                    child_children,
                    child_parsed_types_aggregation,
                    child_shadows,
                ) = parse_props(child, root_lang)
                for key, new_value in child_props.items():
                    prop_value = properties.get(key, [])
                    prop_value.extend(new_value)
                    properties[key] = prop_value
                children.extend(child_children)
                parsed_types_aggregation.update(child_parsed_types_aggregation)
                if shadow is not None:
                    for key, new_shadows in child_shadows.items():
                        if key == "__children__":
                            shadow.children.extend(new_shadows)
                        else:
                            shadow.properties.setdefault(key, []).extend(new_shadows)

            # complex h-* objects can take their "value" from the
            # first explicit property ("name" for p-* or "url" for u-*)
            if value_property and value_property in properties:
                simple_value = properties[value_property][0]

            # if some properties not already found find in implied ways unless in backcompat mode
            if not backcompat_mode:
                # stop implied name if any p-*, e-*, h-* is already found
                if "name" not in properties and parsed_types_aggregation.isdisjoint(
                    "peh"
                ):
                    trace = [] if self.audit_enabled else None
                    implied_name = implied_properties.name(
                        el, self.__url__, self.filtered_roots, trace=trace
                    )
                    properties["name"] = [implied_name]
                    if shadow is not None:
                        shadow.properties["name"] = trace

                if "photo" not in properties and parsed_types_aggregation.isdisjoint(
                    "uh"
                ):
                    trace = [] if self.audit_enabled else None
                    x = implied_properties.photo(
                        el,
                        self.__url__,
                        self.filtered_roots,
                        trace=trace,
                    )
                    if x is not None:
                        properties["photo"] = [x]
                        if shadow is not None:
                            shadow.properties["photo"] = trace

                # stop implied url if any u-* or h-* is already found
                if "url" not in properties and parsed_types_aggregation.isdisjoint(
                    "uh"
                ):
                    trace = [] if self.audit_enabled else None
                    x = implied_properties.url(
                        el,
                        self.__url__,
                        self.filtered_roots,
                        trace=trace,
                    )
                    if x is not None:
                        properties["url"] = [x]
                        if shadow is not None:
                            shadow.properties["url"] = trace

            # build microformat with type and properties
            microformat = {
                "type": [class_name for class_name in sorted(root_class_names)],
                "properties": properties,
            }
            if el.name == "area":
                shape = get_attr(el, "shape")
                if shape is not None:
                    microformat["shape"] = shape

                coords = get_attr(el, "coords")
                if coords is not None:
                    microformat["coords"] = coords

            # insert children if any
            if children:
                microformat["children"] = children

            Id = get_attr(el, "id")
            if Id:
                microformat["id"] = Id

            # simple value is the parsed property value if it were not
            # an h-* class
            if simple_value is not None:
                if isinstance(simple_value, dict):
                    # for e-* properties, the simple value will be
                    # {"html":..., "value":...}  which we should fold
                    # into the microformat object
                    # details: https://github.com/microformats/mf2py/issues/35
                    microformat.update(simple_value)
                else:
                    microformat["value"] = simple_value

            if root_lang:
                microformat["lang"] = root_lang
            elif self.lang:
                microformat["lang"] = self.lang
            return microformat, shadow

        def parse_props(el, root_lang):
            """Parse the properties from a single element.

            Returns (props, children, parsed_types_aggregation, shadows);
            ``shadows`` maps property names to provenance leaves/entities and
            uses the "__children__" key for child microformat shadows.
            """
            props = {}
            children = []
            # for processing implied properties: collects if property types (p, e, u, d(t)) or children (h) have been processed
            parsed_types_aggregation = set()
            shadows = {}
            if self.audit_enabled:
                shadows["__children__"] = []

            def attach(prop_name, leaf_or_entity):
                if self.audit_enabled:
                    shadows.setdefault(prop_name, []).append(leaf_or_entity)

            classes = el.get("class", [])
            filtered_classes = mf2_classes.filter_classes(classes)
            # Is this element a microformat2 root?
            root_class_names = filtered_classes["h"]
            backcompat_mode = False

            # Is this element a microformat1 root?
            if not root_class_names:
                root_class_names = backcompat.root(classes)
                backcompat_mode = True

            if root_class_names:
                parsed_types_aggregation.add("h")

            # Is this a property element (p-*, u-*, etc.) flag
            # False is default
            is_property_el = False

            # mf1 mapping info recorded on backcompat copies
            mf1_info = (
                backcompat.audit_mf1(self._audit_registry, el)
                if self.audit_enabled
                else None
            )

            # Parse plaintext p-* properties.
            p_value = None
            p_trace = [] if self.audit_enabled else None
            for prop_name in filtered_classes["p"]:
                is_property_el = True
                parsed_types_aggregation.add("p")
                prop_value = props.setdefault(prop_name, [])

                # if value has not been parsed then parse it
                if p_value is None:
                    p_value = parse_property.text(
                        el, base_url=self.__url__, trace=p_trace
                    )

                if root_class_names:
                    mf, entity_shadow = handle_microformat(
                        root_class_names,
                        el,
                        value_property="name",
                        simple_value=p_value,
                        backcompat_mode=backcompat_mode,
                    )
                    prop_value.append(mf)
                    attach(prop_name, entity_shadow)
                else:
                    prop_value.append(p_value)
                    if p_trace is not None:
                        leaf = p_trace[-1]
                        if mf1_info is not None:
                            leaf = dict(leaf, mf1=mf1_info)
                        attach(prop_name, leaf)

            # Parse URL u-* properties.
            u_value = None
            u_trace = [] if self.audit_enabled else None
            for prop_name in filtered_classes["u"]:
                is_property_el = True
                parsed_types_aggregation.add("u")
                prop_value = props.setdefault(prop_name, [])

                # if value has not been parsed then parse it
                if u_value is None:
                    u_value = parse_property.url(
                        el, base_url=self.__url__, trace=u_trace
                    )

                if root_class_names:
                    mf, entity_shadow = handle_microformat(
                        root_class_names,
                        el,
                        value_property="url",
                        simple_value=u_value,
                        backcompat_mode=backcompat_mode,
                    )
                    prop_value.append(mf)
                    attach(prop_name, entity_shadow)
                else:
                    if isinstance(u_value, dict):
                        prop_value.append(u_value)
                    else:
                        prop_value.append(u_value)
                    if u_trace is not None:
                        leaf = u_trace[-1]
                        if mf1_info is not None:
                            leaf = dict(leaf, mf1=mf1_info)
                        attach(prop_name, leaf)

            # Parse datetime dt-* properties.
            dt_value = None
            dt_trace = [] if self.audit_enabled else None
            for prop_name in filtered_classes["dt"]:
                is_property_el = True
                parsed_types_aggregation.add("d")
                prop_value = props.setdefault(prop_name, [])

                # if value has not been parsed then parse it
                if dt_value is None:
                    dt_value, new_date = parse_property.datetime(
                        el, self._default_date, trace=dt_trace
                    )
                    # update the default date
                    if new_date:
                        self._default_date = new_date

                if root_class_names:
                    stops_implied_name = True
                    mf, entity_shadow = handle_microformat(
                        root_class_names,
                        el,
                        simple_value=dt_value,
                        backcompat_mode=backcompat_mode,
                    )
                    prop_value.append(mf)
                    attach(prop_name, entity_shadow)
                else:
                    if dt_value is not None:
                        prop_value.append(dt_value)
                        if dt_trace is not None:
                            leaf = dt_trace[-1]
                            if mf1_info is not None:
                                leaf = dict(leaf, mf1=mf1_info)
                            attach(prop_name, leaf)

            # Parse embedded markup e-* properties.
            e_value = None
            e_trace = [] if self.audit_enabled else None
            for prop_name in filtered_classes["e"]:
                is_property_el = True
                parsed_types_aggregation.add("e")
                prop_value = props.setdefault(prop_name, [])

                # if value has not been parsed then parse it
                if e_value is None:
                    # send original element for parsing backcompat
                    if el.original is None:
                        embedded_el = el
                    else:
                        embedded_el = el.original
                    # provenance must reference the node in the parsed DOM,
                    # not the working copy made when the document is preserved
                    source_el = embedded_el
                    if self._preserve_doc:
                        embedded_el = copy.copy(embedded_el)
                    temp_fixes.rm_templates(embedded_el)
                    e_value = parse_property.embedded(
                        embedded_el,
                        self.__url__,
                        root_lang,
                        self.lang,
                        self.expose_dom,
                        trace=e_trace,
                    )
                    if e_trace is not None:
                        for leaf in e_trace:
                            for src in leaf["sources"]:
                                if src.get("node") is embedded_el:
                                    src["node"] = source_el

                if root_class_names:
                    stops_implied_name = True
                    mf, entity_shadow = handle_microformat(
                        root_class_names,
                        el,
                        simple_value=e_value,
                        backcompat_mode=backcompat_mode,
                    )
                    prop_value.append(mf)
                    attach(prop_name, entity_shadow)
                else:
                    prop_value.append(e_value)
                    if e_trace is not None:
                        leaf = e_trace[-1]
                        if mf1_info is not None:
                            leaf = dict(leaf, mf1=mf1_info)
                        attach(prop_name, leaf)

            # if this is not a property element, but it is a h-* microformat,
            # add it to our list of children
            if not is_property_el and root_class_names:
                mf, entity_shadow = handle_microformat(
                    root_class_names,
                    el,
                    backcompat_mode=backcompat_mode,
                )
                children.append(mf)
                if self.audit_enabled:
                    shadows["__children__"].append(entity_shadow)
            # parse child tags, provided this isn't a microformat root-class
            if not root_class_names:
                for child in get_children(el):
                    (
                        child_properties,
                        child_microformats,
                        child_parsed_types_aggregation,
                        child_shadows,
                    ) = parse_props(child, root_lang)
                    for prop_name in child_properties:
                        v = props.get(prop_name, [])
                        v.extend(child_properties[prop_name])
                        props[prop_name] = v
                    children.extend(child_microformats)
                    parsed_types_aggregation.update(child_parsed_types_aggregation)
                    if self.audit_enabled:
                        for prop_name, leaf_list in child_shadows.items():
                            if prop_name == "__children__":
                                shadows["__children__"].extend(leaf_list)
                            else:
                                shadows.setdefault(prop_name, []).extend(leaf_list)
            return props, children, parsed_types_aggregation, shadows

        def parse_rels(el):
            """Parse an element for rel microformats"""
            rel_attrs = get_attr(el, "rel")
            # if rel attributes exist
            if rel_attrs is not None:
                # find the url and normalise it
                url = try_urljoin(self.__url__, el.get("href", ""))
                value_dict = self.__parsed__["rel-urls"].get(url, {})

                # 1st one wins
                if "text" not in value_dict:
                    value_dict["text"] = el.get_text().strip()

                url_rels = value_dict.get("rels", [])
                value_dict["rels"] = url_rels

                for knownattr in ("media", "hreflang", "type", "title"):
                    x = get_attr(el, knownattr)
                    # 1st one wins
                    if x is not None and knownattr not in value_dict:
                        value_dict[knownattr] = x

                self.__parsed__["rel-urls"][url] = value_dict

                for rel_value in rel_attrs:
                    value_list = self.__parsed__["rels"].get(rel_value, [])
                    if url not in value_list:
                        value_list.append(url)
                    if rel_value not in url_rels:
                        url_rels.append(rel_value)

                    self.__parsed__["rels"][rel_value] = value_list
                if "alternate" in rel_attrs:
                    alternate_list = self.__parsed__.get("alternates", [])
                    alternate_dict = {}
                    alternate_dict["url"] = url
                    x = " ".join([r for r in rel_attrs if not r == "alternate"])
                    if x != "":
                        alternate_dict["rel"] = x
                    alternate_dict["text"] = el.get_text().strip()
                    for knownattr in ("media", "hreflang", "type", "title"):
                        x = get_attr(el, knownattr)
                        if x is not None:
                            alternate_dict[knownattr] = x
                    alternate_list.append(alternate_dict)
                    self.__parsed__["alternates"] = alternate_list

        def parse_el(el, ctx):
            """Parse an element for microformats"""
            classes = el.get("class", [])

            # find potential microformats in root classnames h-*
            potential_microformats = mf2_classes.root(classes, self.filtered_roots)

            # if potential microformats found parse them
            if potential_microformats:
                result, shadow = handle_microformat(potential_microformats, el)
                ctx.append(result)
                if self.audit_enabled:
                    self._audit_shadows.append(shadow)
            else:
                # find backcompat root classnames
                potential_microformats = backcompat.root(classes)
                if potential_microformats:
                    result, shadow = handle_microformat(
                        potential_microformats,
                        el,
                        backcompat_mode=True,
                        original_el=el,
                    )
                    ctx.append(result)
                    if self.audit_enabled:
                        self._audit_shadows.append(shadow)
                else:
                    # parse child tags
                    for child in get_children(el):
                        parse_el(child, ctx)

        ctx = []

        if self.__metaformats:
            # extract out a metaformats item, if available
            self.__metaformats_item = metaformats.parse(self.__doc__, url=self.__url__)

        # start parsing at root element of the document
        parse_el(self.__doc__, ctx)
        self.__parsed__["items"] = ctx
        if self.__metaformats and self.__metaformats_item:
            self.__parsed__["items"].append(self.__metaformats_item)
            if self.audit_enabled:
                # metaformats items are out of scope for provenance; keep the
                # shadow list aligned with items by padding with None
                self._audit_shadows.append(None)

        # parse for rel values
        for el in get_descendents(self.__doc__):
            if el.name in ("a", "area", "link") and el.has_attr("rel"):
                parse_rels(el)

        # sort the rels array in rel-urls since this should be unordered set
        for url in self.__parsed__["rel-urls"]:
            if "rels" in self.__parsed__["rel-urls"][url]:
                rels = self.__parsed__["rel-urls"][url]["rels"]
                self.__parsed__["rel-urls"][url]["rels"] = unordered_list(rels)

        # add actual parser used to debug
        # uses builder.NAME from BeautifulSoup
        if self.__html_parser__:
            self.__parsed__["debug"]["markup parser"] = self.__html_parser__
        else:
            self.__parsed__["debug"]["markup parser"] = "unknown"

    def to_dict(self, filter_by_type=None):
        """Get a dictionary version of the parsed microformat document.

        Args:
          filter_by_type (string, optional): only include top-level items of
            the given h-* type. Defaults to None.

        Returns:
            dict: representation of the parsed microformats document
        """
        if filter_by_type is None:
            return self.__parsed__
        else:
            return [x for x in self.__parsed__["items"] if filter_by_type in x["type"]]

    def audit(self, filter_by_type=None):
        """Return provenance entries for the parsed properties.

        Only available when the parser was constructed with ``audit=True``;
        the regular parse result (``to_dict``/``to_json``) is never modified
        and contains no audit markers.

        For every element of every ``properties`` array of every returned
        h-* item (including nested entities and child items), an entry is
        returned with:

        * ``pointer`` (:rfc:`6901` JSON Pointer): location of the value in
          the document returned by :meth:`to_dict`. When *filter_by_type* is
          given, pointers are relative to the filtered list and still match
          the values actually returned.
        * ``rule`` (string): machine-readable extraction rule, e.g.
          ``p-text``, ``u-attr-href``, ``dt-value-class-pattern``,
          ``e-embedded``, ``implied-name-alt``, ...
        * ``implied`` (bool): whether the value came from an implied-property
          rule.
        * ``mf1`` (dict, only for MF1 backcompat): the MF1 mapping that
          produced the value, e.g. ``{"root": "vcard", "class": "fn"}`` or
          ``{"root": "vcard", "rel": "tag"}``.
        * ``sources`` (list): one dict per contributing raw fragment, in
          contribution order. Each has a ``node_path`` (structural path of
          the originating node in the *original* DOM), an optional
          ``attribute`` (``href``/``alt``/``title``/``datetime``/
          ``value``/``src``/``html``/...), and the raw ``value`` captured
          before URL normalisation or whitespace processing. Different nodes
          with identical text produce separate entries.

        Args:
          filter_by_type (string, optional): same filtering as
            :meth:`to_dict`; returned pointers correspond to the filtered
            result.

        Returns:
          list[dict]: provenance entries, sorted by JSON Pointer.

        Raises:
          RuntimeError: if auditing was not enabled at construction.
        """
        if not self.audit_enabled:
            raise RuntimeError(
                "provenance is only collected when Parser is constructed "
                "with audit=True"
            )

        if self._audit_path_map is None:
            self._audit_path_map = build_node_path_map(self.__doc__)

        if filter_by_type is None:
            return audit_entries(
                self._audit_shadows,
                self.__parsed__["items"],
                self._audit_path_map,
                pointer_prefix=("items",),
                registry=self._audit_registry,
            )

        # compute pointers against the filtered result list itself, while
        # keeping each entry aligned with the matching item through its shadow.
        # Pair full-document indices up front so duplicate items can never be
        # resolved to the wrong shadow.
        items = self.__parsed__["items"]
        filtered_pairs = [
            (full_index, item)
            for full_index, item in enumerate(items)
            if filter_by_type in item["type"]
        ]

        out = []
        for filtered_index, (full_index, item) in enumerate(filtered_pairs):
            entries = audit_entries(
                [self._audit_shadows[full_index]],
                [item],
                self._audit_path_map,
                pointer_prefix=(),
                registry=self._audit_registry,
            )
            # audit_entries produced pointers relative to a single-item list
            # ("/0/..."); renumber to the position in the filtered list
            for entry in entries:
                parts = entry["pointer"].split("/", 2)
                entry["pointer"] = (
                    "/"
                    + str(filtered_index)
                    + (("/" + parts[2]) if len(parts) > 2 else "")
                )
                out.append(entry)
        out.sort(key=lambda e: e["pointer"])
        return out

    def to_json(self, pretty_print=False, filter_by_type=None, include_audit=False):
        """Get a json-encoding string version of the parsed microformat document

        Args:
          pretty_print (bool, optional): Encode the json document with
            linebreaks and indents to improve readability. Defaults to False.
          filter_by_type (bool, optional): only include top-level items of
            the given h-* type
          include_audit (bool, optional): when True (and the parser was built
            with ``audit=True``), wrap the document in an object with the
            regular parse result under ``"document"`` and the provenance
            entries under ``"audit"``. Defaults to False, in which case output
            is identical to the non-audited parser.

        Returns:
            string: a json-encoded string
        """

        if include_audit:
            payload = {
                "document": self.to_dict(filter_by_type),
                "audit": self.audit(filter_by_type),
            }
        else:
            payload = self.to_dict(filter_by_type)

        if pretty_print:
            return json.dumps(payload, indent=4, separators=(", ", ": "))
        else:
            return json.dumps(payload)
