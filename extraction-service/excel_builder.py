"""
excel_builder.py

Builds the final Excel workbook from reviewed/edited, grouped line items
(sent from the frontend after a human has checked the extraction and
entered prices). Kept separate from pdf_parser.py / ocr_table.py - this
module knows nothing about PDFs, only about turning sections of items
into a formatted, formula-driven .xlsx.

Two pricing models, chosen by the user in the browser:
  "A" - simple: DESIGNATION | U | QTE | PU/HT | MONTANT HT
  "B" - Fourniture / Mise en Oeuvre: DESIGNATION | UNITE | QTE |
        FOURNITURE(PU, MONTANT) | MISE EN OEUVRE(PU, MONTANT) |
        TOTAL (FOUR + MEO)

Grayscale only, by request - no color fills or colored text anywhere.
"""
from io import BytesIO
import math
import textwrap
from math import ceil
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins

TITLE_FONT = Font(name="Cambria", bold=True, size=20)
LABEL_RICH_FONT = InlineFont(rFont="Cambria", sz="11", b=True)
VALUE_RICH_FONT = InlineFont(rFont="Cambria", sz="11", b=False)
HEADER_FONT = Font(name="Cambria", bold=True, size=11)
NORMAL_FONT = Font(name="Cambria", size=11)

# Grayscale palette only - no color, per request
TABLE_HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")
SECTION_FILL = PatternFill("solid", fgColor="EFEFEF")
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
PRICE_FILL = PatternFill("solid", fgColor="F7F7F7")  # PU/Montant cells, subtle
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
TITLE_BORDER = Border(bottom=Side(style="medium", color="404040"))

# Column layout per model - last column letter, widths, and page
# orientation. Model A (5 narrow columns) reads as an empty landscape
# page even when nothing is wrong - portrait actually fills it. Model B
# (8 columns with grouped headers) genuinely needs the extra landscape
# width. Widths are tuned so the table spans close to the full page
# width in each case, not just a narrow strip on the left.
_LAYOUT = {
    "A": {"last_col": "E", "orientation": "portrait",
          "widths": {"A": 50, "B": 12, "C": 12, "D": 16, "E": 18}},
    "B": {"last_col": "H", "orientation": "portrait",
          "widths": {"A": 46, "B": 11, "C": 10, "D": 14, "E": 16,
                     "F": 14, "G": 16, "H": 18}},
}


def _to_number(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _border_row(ws, r, last_col):
    for col_idx in range(1, ord(last_col) - ord("A") + 2):
        ws.cell(row=r, column=col_idx).border = BORDER


def _write_header_fields(ws, r, fields, last_col):
    """
    One devis header row, rendered as a single merged line with
    'Label : Value' segments side by side, matching the original
    document's layout instead of one-field-per-row. Labels render bold,
    values normal weight, via rich text runs in the one merged cell.
    """
    present = [f for f in fields if (f.get("label") or "").strip() or (f.get("value") or "").strip()]
    if not present:
        return
    blocks = []
    for i, f in enumerate(present):
        label = (f.get("label") or "").strip()
        value = (f.get("value") or "").strip()
        if i > 0:
            blocks.append(TextBlock(VALUE_RICH_FONT, "     "))
        if label:
            blocks.append(TextBlock(LABEL_RICH_FONT, f"{label} : "))
        blocks.append(TextBlock(VALUE_RICH_FONT, value))
    ws.merge_cells(f"A{r}:{last_col}{r}")
    ws[f"A{r}"] = CellRichText(*blocks)


def _write_table_header(ws, r, model):
    """Returns the row number where item data actually starts."""
    if model == "B":
        # Two-row header: single cells vertically merged (A,B,C,H),
        # grouped cells horizontally merged with PU/Montant sub-headers
        # (Fourniture over D:E, Mise en Oeuvre over F:G).
        labels_vertical = {"A": "DESIGNATION DU MATERIEL", "B": "UNITE",
                            "C": "QTE", "H": "TOTAL\n(FOUR + MEO)"}
        for col, label in labels_vertical.items():
            ws.merge_cells(f"{col}{r}:{col}{r+1}")
            c = ws[f"{col}{r}"]
            c.value = label
            c.font = HEADER_FONT
            c.fill = TABLE_HEADER_FILL
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for group_col, label in (("D", "FOURNITURE"), ("F", "MISE EN OEUVRE")):
            end_col = "E" if group_col == "D" else "G"
            ws.merge_cells(f"{group_col}{r}:{end_col}{r}")
            c = ws[f"{group_col}{r}"]
            c.value = label
            c.font = HEADER_FONT
            c.fill = TABLE_HEADER_FILL
            c.alignment = Alignment(horizontal="center", vertical="center")

        for col, label in (("D", "PU"), ("E", "MONTANT"), ("F", "PU"), ("G", "MONTANT")):
            c = ws[f"{col}{r+1}"]
            c.value = label
            c.font = HEADER_FONT
            c.fill = TABLE_HEADER_FILL
            c.alignment = Alignment(horizontal="center", vertical="center")

        _border_row(ws, r, "H")
        _border_row(ws, r + 1, "H")
        return r + 2
    else:
        headers = ["DESIGNATION", "U", "QTE", "PU/HT", "MONTANT HT"]
        for i, h in enumerate(headers, start=1):
            c = ws.cell(row=r, column=i, value=h)
            c.font = HEADER_FONT
            c.fill = TABLE_HEADER_FILL
            c.alignment = Alignment(horizontal="center", vertical="center")
        _border_row(ws, r, "E")
        return r + 1


def build_devis_workbook(sections, header_rows=None, model="A", arrete_text="") -> BytesIO:
    """
    sections: list of {"label": str|None, "items": [item dicts]}
    item dict for model "A": {"description","unit","qty","pu"}
    item dict for model "B": {"description","unit","qty","pu_four","pu_meo"}
    header_rows: optional list of rows, each row a list of
                 {"label": str, "value": str} - the per-tender metadata
                 block (AO N°, client, commune, etc.).
    Returns an in-memory .xlsx file (BytesIO) - nothing written to disk.

    Each section gets its own subtotal (summed over just its own items),
    and the grand TOTAL HT is the sum of every section's subtotal.
    """
    header_rows = header_rows or []
    layout = _LAYOUT.get(model, _LAYOUT["A"])
    last_col = layout["last_col"]
    total_col = last_col  # the rightmost column always holds the row's grand value

    wb = Workbook()
    ws = wb.active
    ws.title = "Devis"
    for col, w in layout["widths"].items():
        ws.column_dimensions[col].width = w

    # Page setup so PDF export (or printing) keeps every column on one
    # page width instead of splitting the table across pages - without
    # this, LibreOffice's default print area cuts the sheet wherever
    # the columns overflow a portrait page, which for an 8-column Model
    # B sheet put the descriptions on page 1 and all the prices on
    # page 2 - unusable as an actual document.
    ws.page_setup.orientation = layout["orientation"]
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5, header=0.2, footer=0.2)

    r = 1
    ws.merge_cells(f"A{r}:{last_col}{r}")
    title_cell = ws[f"A{r}"]
    title_cell.value = "DEVIS"
    title_cell.font = TITLE_FONT
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[r].height = 34
    for col_idx in range(1, ord(last_col) - ord("A") + 2):
        ws.cell(row=r, column=col_idx).border = TITLE_BORDER
    r += 2

    for field_row in header_rows:
        _write_header_fields(ws, r, field_row, last_col)
        r += 1
    r += 1

    r = _write_table_header(ws, r, model)

    subtotal_rows = []
    for section in sections:
        label = (section.get("label") or "").strip()
        items = section.get("items") or []

        if label:
            ws.merge_cells(f"A{r}:{last_col}{r}")
            c = ws[f"A{r}"]
            c.value = label
            c.font = HEADER_FONT
            c.fill = SECTION_FILL
            _border_row(ws, r, last_col)
            # Give the cells more vertical breathing room
            for col in range(1, ord(last_col) - ord("A") + 2):
              ws.cell(row=r, column=col).alignment = Alignment(
              vertical="center",
              wrap_text=True
            )

            ws.row_dimensions[r].height = 24
            r += 1

        first_row = r
        for item in items:
            ws.cell(row=r, column=1, value=item.get("description", "")).font = NORMAL_FONT
            ws.cell(row=r, column=2, value=item.get("unit", "")).font = NORMAL_FONT
            ws.cell(row=r, column=3, value=_to_number(item.get("qty")) or item.get("qty", "")).font = NORMAL_FONT

            if model == "B":
                pu_four = ws.cell(row=r, column=4, value=_to_number(item.get("pu_four")))
                pu_four.fill = PRICE_FILL
                montant_four = ws.cell(row=r, column=5, value=f'=IF(D{r}="","",C{r}*D{r})')
                montant_four.fill = PRICE_FILL
                pu_meo = ws.cell(row=r, column=6, value=_to_number(item.get("pu_meo")))
                pu_meo.fill = PRICE_FILL
                montant_meo = ws.cell(row=r, column=7, value=f'=IF(F{r}="","",C{r}*F{r})')
                montant_meo.fill = PRICE_FILL
                total_cell = ws.cell(row=r, column=8, value=f'=IF(AND(D{r}="",F{r}=""),"",E{r}+G{r})')
                total_cell.fill = PRICE_FILL
            else:
                pu_cell = ws.cell(row=r, column=4, value=_to_number(item.get("pu")))
                pu_cell.fill = PRICE_FILL
                montant_cell = ws.cell(row=r, column=5, value=f'=IF(D{r}="","",C{r}*D{r})')
                montant_cell.fill = PRICE_FILL

            price_cols = (4, 5, 6, 7, 8) if model == "B" else (4, 5)
            for col in price_cols:
                ws.cell(row=r, column=col).number_format = "#,##0.00"

            # Give the cells more vertical breathing room
            for col in range(1, ord(last_col) - ord("A") + 2):
                ws.cell(row=r, column=col).alignment = Alignment(
                vertical="center",
                wrap_text=True
            )
                
            desc = str(item.get("description") or "")
            chars_per_line = max(1, int(layout["widths"]["A"] * 1.0))
            n_lines = sum(
                max(1, len(textwrap.wrap(p, width=chars_per_line)))
                for p in (desc.splitlines() or [""])
            )
            ws.row_dimensions[r].height = min(409, max(24, n_lines * 14.5 + 4))

            _border_row(ws, r, last_col)
            r += 1
        last_row = r - 1

        if last_row >= first_row:
            second_last_col = get_column_letter(ord(last_col) - ord("A"))  # one before last_col
            ws.merge_cells(f"A{r}:{second_last_col}{r}")
            sub_label = f"S/Total {label}" if label else "S/Total"
            ws[f"A{r}"] = sub_label
            ws[f"A{r}"].font = HEADER_FONT
            ws[f"A{r}"].fill = TOTAL_FILL
            ws.cell(row=r, column=ord(total_col) - ord("A") + 1,
                     value=f"=SUM({total_col}{first_row}:{total_col}{last_row})")
            _border_row(ws, r, last_col)
            # Give the cells more vertical breathing room
            for col in range(1, ord(last_col) - ord("A") + 2):
                ws.cell(row=r, column=col).alignment = Alignment(
                vertical="center",
                wrap_text=True
            )
            ws.row_dimensions[r].height = 24
            subtotal_rows.append(r)
            r += 1
        r += 1  # blank spacer between sections

    second_last_col = get_column_letter(ord(last_col) - ord("A"))
    ws.merge_cells(f"A{r}:{second_last_col}{r}")
    ws[f"A{r}"] = "TOTAL HT"
    ws[f"A{r}"].font = HEADER_FONT
    ws[f"A{r}"].fill = TOTAL_FILL
    total_ht_row = r
    if subtotal_rows:
        refs = ",".join(f"{total_col}{x}" for x in subtotal_rows)
        ws.cell(row=r, column=ord(total_col) - ord("A") + 1, value=f"=SUM({refs})")
    _border_row(ws, r, last_col)
                               # Give the cells more vertical breathing room
    for col in range(1, ord(last_col) - ord("A") + 2):
        ws.cell(row=r, column=col).alignment = Alignment(
        vertical="center",
        wrap_text=True
    )
    ws.row_dimensions[r].height = 24
    r += 1

    ws.merge_cells(f"A{r}:{second_last_col}{r}")
    ws[f"A{r}"] = "TVA 19%"
    ws[f"A{r}"].font = HEADER_FONT
    ws.cell(row=r, column=ord(total_col) - ord("A") + 1, value=f"={total_col}{total_ht_row}*0.19")
    _border_row(ws, r, last_col)
                               # Give the cells more vertical breathing room
    for col in range(1, ord(last_col) - ord("A") + 2):
        ws.cell(row=r, column=col).alignment = Alignment(
        vertical="center",
        wrap_text=True
    )
    ws.row_dimensions[r].height = 24
    tva_row = r
    r += 1

    ws.merge_cells(f"A{r}:{second_last_col}{r}")
    ws[f"A{r}"] = "TTC"
    ws[f"A{r}"].font = HEADER_FONT
    ws[f"A{r}"].fill = TOTAL_FILL
    ws.cell(row=r, column=ord(total_col) - ord("A") + 1,
             value=f"={total_col}{total_ht_row}+{total_col}{tva_row}")
    _border_row(ws, r, last_col)
                               # Give the cells more vertical breathing room
    for col in range(1, ord(last_col) - ord("A") + 2):
        ws.cell(row=r, column=col).alignment = Alignment(
        vertical="center",
        wrap_text=True
    )
    ws.row_dimensions[r].height = 24
    r += 2

    if (arrete_text or "").strip():
        ws.merge_cells(f"A{r}:{last_col}{r}")
        ws[f"A{r}"] = CellRichText(
            TextBlock(LABEL_RICH_FONT, "Ce devis est arrete a la somme de : "),
            TextBlock(VALUE_RICH_FONT, arrete_text.strip()),
        )
        r += 2

    ws.merge_cells(f"A{r}:{last_col}{r}")
    
    for _r in subtotal_rows + [total_ht_row, tva_row, tva_row + 1]:
        ws.cell(row=_r, column=ord(total_col) - ord("A") + 1).number_format = "#,##0.00"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
