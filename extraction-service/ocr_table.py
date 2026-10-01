"""
ocr_table.py

Reconstructs a table from a scanned/photographed PDF page, using word
bounding boxes from Tesseract rather than trusting raw reading order.

Two-stage approach:
  1. Column detection: project every word's horizontal extent onto the
     x-axis. Where that "ink coverage" has a persistent gap, that's a
     column boundary. This is layout-derived, not vocabulary-derived -
     works even if header wording differs between document variants.
  2. Row reconstruction: the widest column is assumed to be the
     (possibly multi-line) description column. Every OTHER column is
     assumed short/single-line, so its word positions anchor row
     boundaries. Description text between two consecutive anchors -
     however many OCR lines it spans - is merged into that row.
"""
from pdf2image import convert_from_path
import pytesseract
from pytesseract import Output

HEADER_TOKENS = {"DESIGNATION", "RUBRIQUE", "CONSISTANCE", "QTE", "QTÉ",
                  "PRIX", "UNITAIRE", "MONTANT", "OUVRAGES"}
END_TOKENS = {"TOTAL"}


def _get_words(image, lang="fra", min_conf=30):
    data = pytesseract.image_to_data(image, lang=lang, output_type=Output.DICT)
    words = []
    for i in range(len(data["text"])):
        text = data["text"][i].strip()
        try:
            conf = int(float(data["conf"][i]))
        except ValueError:
            conf = -1
        if text and conf >= min_conf:
            words.append({
                "text": text,
                "left": data["left"][i], "top": data["top"][i],
                "width": data["width"][i], "height": data["height"][i],
            })
    return words


def _find_table_band(words, tol=15):
    """
    Locate the vertical (y) extent of the actual table body, so column
    detection isn't polluted by the page title, header block, or
    signature line above/below the table.
    Returns (y_top, y_bottom) or None if no table markers were found.
    """
    header_hits = [w for w in words if w["text"].upper() in HEADER_TOKENS]
    if len(header_hits) < 2:
        return None

    # A single stray header-vocabulary word can appear outside the real
    # table (e.g. "DESIGNATION" inside a title like "DESIGNATION AFFAIRE").
    # The real header line is the one where SEVERAL header tokens cluster
    # together vertically - so group hits by proximity and keep the
    # largest cluster.
    header_hits.sort(key=lambda w: w["top"])
    clusters, current = [], [header_hits[0]]
    for w in header_hits[1:]:
        if w["top"] - current[-1]["top"] <= 60:
            current.append(w)
        else:
            clusters.append(current)
            current = [w]
    clusters.append(current)
    best_cluster = max(clusters, key=len)
    if len(best_cluster) < 2:
        return None

    end_tops = [w["top"] for w in words if w["text"].upper() in END_TOKENS]
    # Scan is often skewed, so anchor on the EARLIEST word in the real
    # header cluster, not the latest - using the latest risks slicing
    # into row 1 before it even starts.
    header_bottom = min(w["top"] for w in best_cluster) + 15
    table_end = min([t for t in end_tops if t > header_bottom], default=None)
    if table_end is None:
        table_end = max(w["top"] + w["height"] for w in words)
    return (header_bottom, table_end)


def _detect_columns(words, page_width, min_gap=25):
    """Find column x-ranges by locating persistent whitespace gaps."""
    if not words:
        return []
    covered = [False] * (page_width + 1)
    for w in words:
        for x in range(max(0, w["left"]), min(page_width, w["left"] + w["width"]) + 1):
            covered[x] = True

    bands = []
    start = None
    gap_len = 0
    for x in range(page_width + 1):
        if covered[x]:
            if start is None:
                start = x
            gap_len = 0
        else:
            gap_len += 1
            if start is not None and gap_len >= min_gap:
                bands.append((start, x - gap_len))
                start = None
    if start is not None:
        bands.append((start, page_width))
    return bands


def _assign_column(word, bands):
    center = word["left"] + word["width"] / 2
    for i, (lo, hi) in enumerate(bands):
        if lo <= center <= hi:
            return i
    return len(bands) - 1


def _order_wrapped_text(words, line_tol=15):
    """
    Words in a (possibly multi-line) description cell, correctly ordered.
    Naively sorting by (top, left) breaks on skewed scans, where two
    words on the SAME visual line can have slightly different 'top'
    values - so we first cluster into sub-lines by top-proximity, sort
    each sub-line left-to-right, then stack sub-lines top-to-bottom.
    """
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: w["top"])
    lines, current = [], [ordered[0]]
    for w in ordered[1:]:
        if w["top"] - current[-1]["top"] <= line_tol:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    lines.append(current)
    parts = []
    for line in lines:
        line.sort(key=lambda w: w["left"])
        parts.append(" ".join(w["text"] for w in line))
    return " ".join(parts)


def reconstruct_rows(image, lang="fra"):
    words = _get_words(image, lang=lang)
    if not words:
        return []

    band = _find_table_band(words)
    if band is None:
        return []
    table_words = [w for w in words if band[0] <= w["top"] <= band[1]]
    if not table_words:
        return []

    page_width = image.width
    bands = _detect_columns(table_words, page_width)
    if len(bands) < 2:
        return []

    for w in table_words:
        w["col"] = _assign_column(w, bands)

    coverage = {i: 0 for i in range(len(bands))}
    for w in table_words:
        coverage[w["col"]] += w["width"]
    desc_col = max(coverage, key=coverage.get)

    def is_noise(w):
        t = w["text"].upper()
        return t in HEADER_TOKENS or t in END_TOKENS or not any(c.isalnum() for c in t)

    data_words = [w for w in table_words if not is_noise(w)]
    anchor_words = [w for w in data_words if w["col"] != desc_col]
    desc_words = [w for w in data_words if w["col"] == desc_col]

    anchor_words.sort(key=lambda w: w["top"])
    row_bands = []
    tol = 12
    for w in anchor_words:
        placed = False
        for band_ in row_bands:
            if abs(w["top"] - band_["y"]) <= tol:
                band_["words"].append(w)
                placed = True
                break
        if not placed:
            row_bands.append({"y": w["top"], "words": [w]})
    row_bands.sort(key=lambda b: b["y"])

    rows = []
    for idx, band_ in enumerate(row_bands):
        y_lo = row_bands[idx - 1]["y"] + tol if idx > 0 else -1
        y_hi = band_["y"] + tol
        desc_in_row = [w for w in desc_words if y_lo < w["top"] <= y_hi]
        desc_text = _order_wrapped_text(desc_in_row)

        row = {"description": desc_text, "columns": {}}
        for w in sorted(band_["words"], key=lambda w: w["left"]):
            row["columns"].setdefault(w["col"], []).append(w["text"])
        row["columns"] = {f"col_{c}": " ".join(v) for c, v in row["columns"].items()}
        rows.append(row)

    return rows


def extract_scanned(path, lang="fra") -> dict:
    images = convert_from_path(str(path), dpi=300)
    all_rows = []
    for image in images:
        all_rows.extend(reconstruct_rows(image, lang=lang))
    return {
        "source_type": "scanned_ocr",
        "item_count": len(all_rows),
        "entries": all_rows,
    }
