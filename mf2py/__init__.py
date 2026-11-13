"""
Microformats2 is a general way to mark up any HTML document with
classes and propeties. This library parses structured data from a
microformatted HTML document and returns a well-formed JSON
dictionary.
"""

from .includes import IncludeError, IncludeLimitExceeded
from .mf_helpers import get_url
from .parser import Parser, parse
from .version import __version__

__all__ = [
    "Parser",
    "parse",
    "get_url",
    "IncludeError",
    "IncludeLimitExceeded",
    "__version__",
]
