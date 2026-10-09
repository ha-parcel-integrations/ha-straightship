"""Parser for the Straightship result page.

The page is server-rendered HTML with no structured alternative, so the parser
fails closed: anything it cannot positively identify raises
:class:`StraightshipParseError` instead of yielding a partial parcel. Cells are
matched on their label text, never on position, and a cell's value is its full
text content because one status sentence embeds a mail link.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

NO_RESULT_MARKER = "No results found!"

_SUMMARY_LABELS = {
    "client code": "client_code",
    "tracking id": "tracking_id",
    "package id": "package_id",
    "package status": "package_status",
}
_HISTORY_HEADER = ("timestamp", "status", "city")
_VOID_TAGS = frozenset(
    {"br", "hr", "img", "input", "link", "meta", "area", "base", "col", "wbr"}
)
_IGNORED_TEXT_TAGS = frozenset({"script", "style"})
_LAST_UPDATED_RE = re.compile(r"^Last Updated:\s*(.*)$", re.IGNORECASE)


class StraightshipParseError(Exception):
    """Raised when the page does not have the expected result shape."""


def clean_text(value: str) -> str:
    """Collapse whitespace (including non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


class _PageParser(HTMLParser):
    """Collect every table as rows of ``(tag, text)`` cells, plus ``p`` texts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, str]]]] = []
        self.paragraphs: list[str] = []
        self._table_depth = 0
        self._row: list[tuple[str, str]] | None = None
        self._cell_tag: str | None = None
        self._cell_parts: list[str] = []
        self._paragraph_parts: list[str] | None = None
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORED_TEXT_TAGS:
            self._ignored += 1
        elif tag == "table":
            self._table_depth += 1
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._close_row()
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._close_cell()
            self._cell_tag = tag
            self._cell_parts = []
        elif tag == "p":
            self._paragraph_parts = []
        elif tag == "br":
            self._text(" ")

    def handle_startendtag(self, tag, attrs):
        if tag not in _VOID_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)
        else:
            self.handle_starttag(tag, attrs)

    def _text(self, data: str) -> None:
        if self._ignored:
            return
        if self._cell_tag is not None:
            self._cell_parts.append(data)
        if self._paragraph_parts is not None:
            self._paragraph_parts.append(data)

    def handle_data(self, data):
        self._text(data)

    def _close_cell(self) -> None:
        if self._cell_tag is not None and self._row is not None:
            self._row.append((self._cell_tag, clean_text("".join(self._cell_parts))))
        self._cell_tag = None
        self._cell_parts = []

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None and self.tables:
            self.tables[-1].append(self._row)
        self._row = None

    def handle_endtag(self, tag):
        if tag in _IGNORED_TEXT_TAGS:
            self._ignored = max(0, self._ignored - 1)
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table" and self._table_depth:
            self._close_row()
            self._table_depth -= 1
        elif tag == "p" and self._paragraph_parts is not None:
            self.paragraphs.append(clean_text("".join(self._paragraph_parts)))
            self._paragraph_parts = None

    def close(self):
        super().close()
        self._close_row()


def _summary(tables: list[list[list[tuple[str, str]]]]) -> dict[str, str]:
    """Read the label-keyed summary, laid out either per row or per column."""
    found: dict[str, str] = {}
    for rows in tables:
        for index, row in enumerate(rows):
            for position, (tag, text) in enumerate(row):
                key = _SUMMARY_LABELS.get(text.lower()) if tag == "th" else None
                if key is None or key in found:
                    continue
                following = row[position + 1 :]
                if following and following[0][0] == "td":
                    found[key] = following[0][1]
                elif index + 1 < len(rows) and len(rows[index + 1]) > position:
                    below = rows[index + 1][position]
                    if below[0] == "td":
                        found[key] = below[1]
    return found


def _history(tables: list[list[list[tuple[str, str]]]]) -> list[dict[str, str]] | None:
    """Return the history rows, or ``None`` when no history table exists."""
    for rows in tables:
        if not rows:
            continue
        header = tuple(text.lower() for tag, text in rows[0] if tag == "th")
        if header != _HISTORY_HEADER:
            continue
        events = []
        for row in rows[1:]:
            cells = [text for tag, text in row if tag == "td"]
            if len(cells) < 2:
                continue
            events.append(
                {
                    "timestamp": cells[0],
                    "status": cells[1],
                    "city": (cells[2] if len(cells) > 2 else "") or None,
                }
            )
        return events
    return None


def parse_page(html: str) -> dict | None:
    """Parse a result page into the structured part of the ``raw`` payload.

    Returns ``None`` for the carrier's "no results" page and raises
    :class:`StraightshipParseError` for anything else without a summary table
    and a history table.
    """
    parser = _PageParser()
    parser.feed(html)
    parser.close()

    summary = _summary(parser.tables)
    history = _history(parser.tables)
    if "package_status" not in summary or "tracking_id" not in summary:
        if NO_RESULT_MARKER in html:
            return None
        raise StraightshipParseError("summary table not found")
    if history is None:
        raise StraightshipParseError("history table not found")

    last_updated = None
    for paragraph in parser.paragraphs:
        match = _LAST_UPDATED_RE.match(paragraph)
        if match:
            last_updated = match.group(1) or None
            break

    def optional(key: str) -> str | None:
        value = summary.get(key, "")
        return None if value in ("", "-") else value

    return {
        "client_code": optional("client_code"),
        "tracking_id": summary["tracking_id"],
        "package_id": optional("package_id"),
        "package_status": summary["package_status"],
        "last_updated": last_updated,
        "history": history,
    }
