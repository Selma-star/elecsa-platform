import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Union

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pdf_parser import is_native_pdf, extract_native
from ocr_table import extract_scanned
from excel_builder import build_devis_workbook

app = FastAPI(title="ELECSA Extraction Service")


class DevisItem(BaseModel):
    description: str = ""
    unit: str = ""
    qty: Union[str, float, int] = ""
    pu: Union[str, float, int] = ""
    pu_four: Union[str, float, int] = ""
    pu_meo: Union[str, float, int] = ""


class DevisSection(BaseModel):
    label: Optional[str] = None
    items: List[DevisItem] = []


class HeaderField(BaseModel):
    label: str = ""
    value: str = ""


class GenerateExcelRequest(BaseModel):
    sections: List[DevisSection]
    header_rows: Optional[List[List[HeaderField]]] = None
    model: str = "A"
    arrete_text: Optional[str] = ""
    filename: Optional[str] = "devis"


def _build_workbook_from_request(req: GenerateExcelRequest):
    sections = [
        {"label": s.label, "items": [i.model_dump() for i in s.items]}
        for s in req.sections
    ]
    header_rows = [[f.model_dump() for f in row] for row in (req.header_rows or [])]
    return build_devis_workbook(sections, header_rows, model=req.model, arrete_text=req.arrete_text)


def _xlsx_to_pdf_bytes(xlsx_buf) -> bytes:
    """
    Converts an in-memory .xlsx to PDF using headless LibreOffice, so the
    PDF is generated from the EXACT SAME workbook as the Excel download -
    one source of truth, not two separately-maintained layouts.
    Requires LibreOffice ('soffice') installed and on PATH.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path = Path(tmpdir) / "devis.xlsx"
        xlsx_path.write_bytes(xlsx_buf.read())

        # Unique --env:UserInstallation avoids profile-lock clashes if
        # two conversions happen to run at the same moment. Built with
        # .as_uri() rather than a manual f-string - a hand-built
        # "file://" + path breaks on Windows (backslashes, missing the
        # third slash before the drive letter) even though the same
        # trick happens to work on Linux by coincidence.
        profile_dir = Path(tmpdir) / "lo_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                [
                    "soffice", "--headless", "--norestore",
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--convert-to", "pdf", "--outdir", tmpdir, str(xlsx_path),
                ],
                capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="LibreOffice ('soffice') is not installed or not on PATH - required for PDF export.",
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="PDF conversion timed out.")

        pdf_path = Path(tmpdir) / "devis.pdf"
        if result.returncode != 0 or not pdf_path.exists():
            raise HTTPException(
                status_code=500,
                detail=f"PDF conversion failed: {result.stderr.strip() or result.stdout.strip()}",
            )
        return pdf_path.read_bytes()


@app.get("/health")
def health():
    return {"status": "ok", "service": "extraction-service"}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Save the upload to a temp file - pdfplumber needs a real file path/handle
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        if is_native_pdf(tmp_path):
            result = extract_native(tmp_path)
        else:
            result = extract_scanned(tmp_path)
        result["filename"] = file.filename
        return result
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/generate-excel")
async def generate_excel(req: GenerateExcelRequest):
    buf = _build_workbook_from_request(req)
    out_name = f"{req.filename}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )


@app.post("/generate-pdf")
async def generate_pdf(req: GenerateExcelRequest):
    buf = _build_workbook_from_request(req)
    pdf_bytes = _xlsx_to_pdf_bytes(buf)
    out_name = f"{req.filename}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )
