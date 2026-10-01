"""
pdf_parser.py

Pure extraction logic, kept separate from the HTTP layer (main.py) so it
can be tested and reused independently.

Two entry points:
  - is_native_pdf(path)   -> bool   (does the PDF have a real text layer?)
  - extract_native(path)  -> dict   (structured line items, native PDFs only)

Scanned/photographed PDFs are detected but not yet processed here - see
ocr_table.py for that path.

IMPORTANT: this classifier is format-agnostic on purpose. Real Sonelgaz
devis vary a lot - some have a short reference code per item ("A-1")
with the real description in the next column, others put the full
description directly in column 0 with no code at all; some have a
single PU/Montant pair, others (Fourniture + Mise en Oeuvre) have two.
Rather than hardcoding one document's shape, rows are classified by
their STRUCTURE (how many cells are filled, and where), not by
matching specific header vocabulary - vocabulary varies too much
across real documents (English vs French wording, "TOTAL HT" vs
"Total en HT :" vs "S/Total", etc).
"""
import re
import pdfplumber

# A short reference code like "A-1", "D-9" - letters, optional hyphen,
# digits, no spaces. Used to tell "col 0 is a code" (M'sila-style) apart
# from "col 0 IS the description" (LOT07-style) - a plain word like
# "IACM" has no digit and won't match, a long phrase has spaces and
# won't match either.
_CODE_PATTERN = re.compile(r"^[A-Za-z]{0,3}-?\d+[A-Za-z]?$")

_TOTAL_KEYWORDS = ("TOTAL", "TVA", "TTC")
_ARRETE_KEYWORDS = ("ARRETE", "ARRÊTÉ", "ARRETÉ", "ARRÊTE")


def is_native_pdf(path) -> bool:
    """A PDF is 'native' if at least one page has an extractable text layer."""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            if len(page.chars) > 0:
                return True
    return False


def _extract_tables(path):
    """List of tables (each a list of rows), across all pages."""
    tables = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if table:
                    tables.append(table)
    return tables


def _looks_like_code(cell):
    return bool(cell) and " " not in cell and len(cell) <= 8 and bool(_CODE_PATTERN.match(cell))


def _parse_qty(raw):
    if not raw:
        return raw
    try:
        return float(str(raw).replace(",", "."))
    except ValueError:
        return raw


def _classify_table(table, skip_header):
    """
    skip_header: True only for the very first table in the document -
    continuation tables (a devis spanning multiple pages) don't repeat
    the header row, so treating every table's row 0 as a header would
    silently eat real data on page 2+.
    """
    structured = []
    if not table:
        return structured

    body = table[1:] if skip_header else table

    for row in body:
        if not row or all(c in (None, "") for c in row):
            continue
        cells = [c.strip() if isinstance(c, str) else c for c in row]
        joined = " ".join(c for c in cells if c).upper()

        if any(k in joined for k in _TOTAL_KEYWORDS):
            value = next((c for c in reversed(cells) if c and re.search(r"\d", c)), None)
            structured.append({"type": "subtotal", "label": joined, "value": value})
            continue

        if any(k in joined for k in _ARRETE_KEYWORDS):
            structured.append({"type": "arrete_line", "text": joined})
            continue

        present = [i for i, c in enumerate(cells) if c not in (None, "")]
        if len(present) <= 2 and present and present[0] == 0:
            # Only col 0 (or col 0 + one more) carries text, nothing in
            # the rest of the row - a section header, whatever its exact
            # wording ("A" + "Armoire RDC", or a full standalone phrase).
            label = " ".join(c for c in cells if c)
            structured.append({"type": "section", "label": label})
            continue

        # Item row. Column 0 is either a short code (M'sila-style) with
        # the real description in column 1, or the description itself
        # (LOT07-style) - detected structurally, not assumed.
        if _looks_like_code(cells[0]) and len(cells) > 1 and cells[1]:
            description = cells[1]
            unit = cells[2] if len(cells) > 2 else ""
            qty_raw = cells[3] if len(cells) > 3 else ""
            extra = cells[4:] if len(cells) > 4 else []
        else:
            description = cells[0]
            unit = cells[1] if len(cells) > 1 else ""
            qty_raw = cells[2] if len(cells) > 2 else ""
            extra = cells[3:] if len(cells) > 3 else []

        if description:
            structured.append({
                "type": "item",
                "label": description,
                "unit": unit or "",
                "qty": _parse_qty(qty_raw),
                "extra_cells": [c for c in extra if c not in (None, "")],
            })

    return structured


def extract_native(path) -> dict:
    """Full extraction pipeline for a native-text PDF. Returns JSON-ready dict."""
    tables = _extract_tables(path)
    structured = []
    for i, table in enumerate(tables):
        structured.extend(_classify_table(table, skip_header=(i == 0)))
    items = [e for e in structured if e["type"] == "item"]
    return {
        "source_type": "native",
        "item_count": len(items),
        "entries": structured,
    }
