"""Markdown from the operator, rendered for guests without running anything.

The legal pages and the FAQ are Markdown the operator writes in the admin
(or an assistant through MCP ``set_legal_texts``) and guests read. The page's
content security policy still allows inline script (SECURITY.md), so HTML in
that text was a stored script (final review A5). Two layers, standard
library only:

1. Raw HTML is escaped before the conversion: ``<`` and ``&`` typed in the
   text are shown as text. Markdown itself needs neither (``>`` stays, it
   starts a quote).
2. The converter's output goes through an allow-list: only the tags Markdown
   produces for headings, paragraphs, emphasis, lists, quotes, code, rules,
   tables and links; no attribute but ``href``/``title`` on links and the
   alignment of table cells; ``href`` only ``http``, ``https``, ``mailto`` or
   a relative address. Everything else is dropped, its text kept.

Second review (R2-U5): 10,000 times ``[a](`` took 17 s, because the
Markdown library looks for the end of every link with a backtracking
pattern. Links are now found here, by one pattern whose parts cannot
overlap (linear), and the library's own link patterns are switched off.
Texts are limited to MAX_CHARS (form, MCP tool and renderer), and a rendered
text is kept in a small cache keyed by its content.
"""
from __future__ import annotations

import functools
import html
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

#: Longest imprint, privacy text, FAQ answer or intro (characters). A long
#: privacy notice has about 15,000; more is refused by the form and the MCP
#: tool, and cut here.
MAX_CHARS = 20000
#: [text](target) — text without brackets or a line break, target without
#: white space, brackets, parentheses or angle brackets. No part can match
#: what the next one needs, so the engine never backtracks more than a step.
#: One level of parentheses inside the target is allowed ("…/Foo_(Bar)"):
#: its opening "(" is a character the plain part cannot take, so still linear.
_LINK = re.compile(r"(!?)\[([^\[\]\n]{0,300})\]\(((?:[^\s()<>\[\]]|\([^\s()<>\[\]]{0,200}\)){1,2000})\)")
#: Stand-ins the library treats as plain text (private use area). ``<`` and
#: ``&`` typed by the operator become entities only after the conversion:
#: thousands of "&lt;" made the library stash each one (slow, R2-U5).
_LINK_OPEN, _LINK_CLOSE, _LT, _AMP = "\ue000", "\ue001", "\ue002", "\ue003"
_TOKEN = re.compile(_LINK_OPEN + r"(\d+)" + _LINK_CLOSE)
#: More than three quote levels say nothing more; 10,000 nested ones kept the
#: library busy for a fifth of a second.
_DEEP_QUOTE = re.compile(r"(?m)^[ \t]{0,3}(?:>[ \t]?){4,}")
#: Line breaks inside a paragraph or list item (what "nl2br" did; the
#: extension builds one element per break, slow for thousands of lines).
_BLOCK_WITH_BREAKS = re.compile(r"<(p|li)>([^<]*(?:<(?!/\1>)[^<]*)*)</\1>")
#: The Markdown library's inline link patterns (backtracking, R2-U5).
_LIB_LINK_PATTERNS = ("link", "image_link", "reference", "image_reference", "short_reference",
                      "short_image_ref", "autolink", "automail")

ALLOWED_TAGS = {"p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i", "code", "pre",
                "ul", "ol", "li", "blockquote", "hr", "a", "table", "thead", "tbody", "tr", "th", "td"}
VOID_TAGS = {"br", "hr"}
#: Whose content goes with them (not shown as text).
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript"}
_ALIGN = re.compile(r"^\s*text-align:\s*(left|right|center);?\s*$")
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")


def safe_href(value: Optional[str]) -> Optional[str]:
    """The link target if it is harmless: http(s), mailto or relative; else ``None``."""
    raw = html.unescape(value or "")
    # Browsers ignore control characters and white space inside a scheme.
    squeezed = re.sub(r"[\x00-\x20\x7f]+", "", raw)
    m = _SCHEME.match(squeezed)
    if m:
        return raw.strip() if m.group(1).lower() in ("http", "https", "mailto") else None
    if squeezed.startswith("//"):
        return None                     # protocol-relative: another host, not a relative link
    return raw.strip()


class _Filter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.dropping = 0
        self.open_links: List[bool] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.dropping += 1
            return
        if self.dropping or tag not in ALLOWED_TAGS:
            if tag == "a":
                self.open_links.append(False)
            return
        kept = []
        for name, value in attrs:
            name = name.lower()
            if tag == "a" and name == "href":
                href = safe_href(value)
                if href is not None:
                    kept.append(("href", href))
            elif tag == "a" and name == "title":
                kept.append(("title", value or ""))
            elif tag in ("th", "td") and name == "align" and (value or "").lower() in ("left", "right", "center"):
                kept.append(("align", value.lower()))
            elif tag in ("th", "td") and name == "style" and _ALIGN.match(value or ""):
                kept.append(("align", _ALIGN.match(value).group(1)))
        if tag == "a":
            self.open_links.append(True)
        text = "".join(f' {n}="{html.escape(v, quote=True)}"' for n, v in kept)
        self.out.append(f"<{tag}{text}>" if tag not in VOID_TAGS else f"<{tag}{text} />")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS and tag.lower() not in DROP_WITH_CONTENT:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.dropping = max(0, self.dropping - 1)
            return
        if tag == "a":
            if self.open_links and self.open_links.pop() and not self.dropping:
                self.out.append("</a>")
            return
        if self.dropping or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(data, quote=False))


def clean_html(fragment: str) -> str:
    """Keep only the allowed tags and attributes of an HTML fragment."""
    parser = _Filter()
    parser.feed(fragment or "")
    parser.close()
    return "".join(parser.out)


def escape_source(text: str) -> str:
    """Raw HTML in the Markdown source becomes text: ``&`` and ``<`` escaped, ``>`` kept for quotes."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;")


def too_long(text: Optional[str]) -> bool:
    """True if ``text`` is longer than a legal text or FAQ answer may be."""
    return len(text or "") > MAX_CHARS


def _extract_links(source: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Replace each ``[text](target)`` by a token; images become their text."""
    links: List[Tuple[str, str]] = []

    def swap(m: "re.Match") -> str:
        if m.group(1):                                  # ![alt](src): no images in these texts
            return m.group(2)
        links.append((m.group(2), m.group(3)))
        return f"{_LINK_OPEN}{len(links) - 1}{_LINK_CLOSE}"

    return _LINK.sub(swap, source), links


def _put_links_back(converted: str, links: List[Tuple[str, str]]) -> str:
    def back(m: "re.Match") -> str:
        k = int(m.group(1))
        if k >= len(links):
            return ""
        label, target = links[k]
        return f'<a href="{html.escape(target, quote=True)}">{html.escape(label, quote=False)}</a>'
    return _TOKEN.sub(back, converted)


def render(text: Optional[str], extensions: Tuple[str, ...] = ("nl2br", "tables")) -> str:
    """Operator Markdown → HTML that is safe to put into a page with ``|safe``."""
    if not text:
        return ""
    return _render_cached(str(text)[:MAX_CHARS], tuple(extensions))


@functools.lru_cache(maxsize=128)
def _render_cached(text: str, extensions: Tuple[str, ...]) -> str:
    # Keyed by the text itself: a changed imprint is a new key, the old entry
    # ages out. Rendering happens once per text, not once per page view.
    source = re.sub("[\ue000-\ue003]", "", text)
    source, links = _extract_links(source)
    source = source.replace("&", _AMP).replace("<", _LT)
    source = _DEEP_QUOTE.sub("> > > ", source)
    breaks = "nl2br" in extensions
    extensions = tuple(e for e in extensions if e != "nl2br")
    try:
        import markdown
        from markdown.extensions import Extension

        class _NoLibraryLinks(Extension):
            def extendMarkdown(self, md):  # noqa: N802 — the library's name
                for name in _LIB_LINK_PATTERNS:
                    if name in md.inlinePatterns:
                        md.inlinePatterns.deregister(name)

        converted = markdown.markdown(source, extensions=list(extensions) + [_NoLibraryLinks()])
    except ImportError:                  # the requirement is there; this is only a net
        converted = "".join(f"<p>{line}</p>" for line in source.split("\n") if line.strip())
    if breaks:
        converted = _BLOCK_WITH_BREAKS.sub(
            lambda m: f"<{m.group(1)}>{m.group(2).strip(chr(10)).replace(chr(10), '<br />' + chr(10))}</{m.group(1)}>",
            converted)
    converted = converted.replace(_LT, "&lt;").replace(_AMP, "&amp;")
    return clean_html(_put_links_back(converted, links))
