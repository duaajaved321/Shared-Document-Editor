"""File upload validation, parsing (.txt, .md, .docx -> clean HTML), and HTML sanitization."""

from __future__ import annotations

import html
import io
from html.parser import HTMLParser
from pathlib import Path

import docx
import markdown

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx"}

ALLOWED_TAGS = {
    "p",
    "br",
    "h1",
    "h2",
    "h3",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
}
VOID_TAGS = {"br"}
IGNORED_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "head", "title"}
HEADING_REMAP = {"h4": "h3", "h5": "h3", "h6": "h3"}


class _CleanHTMLSanitizer(HTMLParser):
    """Allowlist HTML sanitizer producing clean semantic markup for Quill.js."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower in IGNORED_CONTENT_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth > 0:
            return
        mapped = HEADING_REMAP.get(lower, lower)
        if mapped in ALLOWED_TAGS:
            self._parts.append(f"<{mapped}>")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in IGNORED_CONTENT_TAGS:
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return
        if self._ignored_depth > 0:
            return
        mapped = HEADING_REMAP.get(lower, lower)
        if mapped in ALLOWED_TAGS and mapped not in VOID_TAGS:
            self._parts.append(f"</{mapped}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignored_depth > 0:
            return
        lower = tag.lower()
        if lower in VOID_TAGS:
            self._parts.append(f"<{lower}>")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and data:
            self._parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self._parts).strip()


def sanitize_html(raw_html: str) -> str:
    """Sanitize rich-text HTML so only safe formatting tags supported by the editor remain."""
    if not raw_html or not raw_html.strip():
        return ""
    parser = _CleanHTMLSanitizer()
    parser.feed(raw_html)
    parser.close()
    return parser.get_html()


def extract_title_from_filename(filename: str) -> str:
    """Derive a clean document title from an uploaded filename without its extension."""
    base_name = Path(filename).name
    stem = Path(base_name).stem.strip()
    return stem if stem else "Imported Document"


def _decode_text_bytes(raw_bytes: bytes) -> str:
    """Decode UTF-8 (with optional BOM) or fallback Latin-1 text safely, rejecting binary bytes."""
    if b"\x00" in raw_bytes:
        raise ValueError("File appears to be binary rather than valid UTF-8 text.")
    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Text file must be UTF-8 encoded.") from exc


def _parse_txt(raw_bytes: bytes) -> str:
    text = _decode_text_bytes(raw_bytes).replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("Uploaded file has no readable text content.")

    paragraphs = [block.strip("\n") for block in text.split("\n\n") if block.strip()]
    html_blocks: list[str] = []
    for paragraph in paragraphs:
        escaped_lines = [html.escape(line) for line in paragraph.split("\n")]
        html_blocks.append(f"<p>{'<br>'.join(escaped_lines)}</p>")
    return "".join(html_blocks)


def _parse_md(raw_bytes: bytes) -> str:
    text = _decode_text_bytes(raw_bytes).replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("Uploaded Markdown file has no readable content.")

    rendered = markdown.markdown(
        text,
        extensions=["sane_lists", "fenced_code", "nl2br"],
    )
    cleaned = sanitize_html(rendered)
    if not cleaned:
        raise ValueError("Uploaded Markdown file produced no editable content.")
    return cleaned


def _format_docx_runs(paragraph: docx.text.paragraph.Paragraph) -> str:
    """Convert a python-docx paragraph's runs into escaped inline HTML."""
    fragments: list[str] = []
    for run in paragraph.runs:
        if not run.text:
            continue
        segment = html.escape(run.text).replace("\n", "<br>")
        if run.bold:
            segment = f"<strong>{segment}</strong>"
        if run.italic:
            segment = f"<em>{segment}</em>"
        if run.underline:
            segment = f"<u>{segment}</u>"
        fragments.append(segment)

    if fragments:
        return "".join(fragments)
    return html.escape(paragraph.text) if paragraph.text else ""


def _detect_docx_block_kind(paragraph: docx.text.paragraph.Paragraph) -> str:
    """Classify a Word paragraph as h1/h2/h3, ul, ol, or p."""
    style_name = (paragraph.style.name if paragraph.style and paragraph.style.name else "").lower()

    if style_name.startswith("heading 1") or style_name == "title":
        return "h1"
    if style_name.startswith("heading 2") or style_name == "subtitle":
        return "h2"
    if style_name.startswith("heading 3") or style_name.startswith("heading 4"):
        return "h3"
    if "list bullet" in style_name or "bullet" in style_name:
        return "ul"
    if "list number" in style_name:
        return "ol"

    # Also detect Word toolbar lists stored via XML numbering properties (w:numPr).
    p_pr = paragraph._p.pPr
    if p_pr is not None and p_pr.numPr is not None:
        return "ul"

    return "p"


def _parse_docx(raw_bytes: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise ValueError("Invalid or corrupted .docx file.") from exc

    blocks: list[str] = []
    active_list: str | None = None

    def close_active_list() -> None:
        nonlocal active_list
        if active_list is not None:
            blocks.append(f"</{active_list}>")
            active_list = None

    for paragraph in document.paragraphs:
        inner_html = _format_docx_runs(paragraph).strip()
        if not inner_html:
            continue

        kind = _detect_docx_block_kind(paragraph)
        if kind in ("ul", "ol"):
            if active_list != kind:
                close_active_list()
                active_list = kind
                blocks.append(f"<{kind}>")
            blocks.append(f"<li>{inner_html}</li>")
        else:
            close_active_list()
            blocks.append(f"<{kind}>{inner_html}</{kind}>")

    close_active_list()

    # Extract text from tables if present so table-based Word docs are also supported.
    for table in document.tables:
        for row in table.rows:
            cells = [html.escape(cell.text.strip()) for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(f"<p>{' | '.join(cells)}</p>")

    if not blocks:
        raise ValueError("Uploaded .docx file contains no readable text.")

    return sanitize_html("".join(blocks))


def parse_uploaded_file(filename: str | None, raw_bytes: bytes) -> tuple[str, str]:
    """Validate an uploaded file and return `(document_title, clean_html)`.

    Raises `ValueError` with a user-friendly message on invalid extension, size, or content.
    """
    if not filename or not filename.strip():
        raise ValueError("Filename is missing.")

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type '{ext or 'none'}'. Supported types: {supported}.")

    if len(raw_bytes) == 0:
        raise ValueError("Uploaded file is empty.")

    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        raise ValueError("File exceeds the maximum allowed size of 5 MB.")

    title = extract_title_from_filename(filename)

    if ext == ".txt":
        content_html = _parse_txt(raw_bytes)
    elif ext == ".md":
        content_html = _parse_md(raw_bytes)
    else:
        content_html = _parse_docx(raw_bytes)

    return title, content_html
