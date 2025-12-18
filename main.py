import os, io, zipfile
from typing import List
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import Response, JSONResponse
from docx import Document
from reportlab.pdfgen import canvas

app = FastAPI(title="Lecture Notes Backend", version="0.1.0")

@app.get("/health")
def health():
    return {"ok": True}

def make_docx(filenames: List[str]) -> bytes:
    doc = Document()
    doc.add_heading("Lecture Notes (Stub)", level=1)
    doc.add_paragraph("Backend received these files:")
    for n in filenames:
        doc.add_paragraph(f"• {n}")
    doc.add_paragraph("\nNext: replace stub with extraction + AI pipeline.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

def make_pdf(filenames: List[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    c.drawString(50, y, "Lecture Notes (Stub)")
    y -= 30
    c.drawString(50, y, "Backend received these files:")
    y -= 25
    for n in filenames:
        c.drawString(70, y, f"- {n}")
        y -= 18
        if y < 60:
            c.showPage()
            y = 800
    c.showPage()
    c.save()
    return buf.getvalue()

@app.post("/make-notes")
async def make_notes(files: List[UploadFile] = File(...)):
    # Read files (we don't process yet; this proves upload plumbing)
    filenames = [f.filename or "upload" for f in files]
    for f in files:
        await f.read()

    docx_bytes = make_docx(filenames)
    pdf_bytes = make_pdf(filenames)

    # Return both files as a ZIP (easy for n8n)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("notes.docx", docx_bytes)
        z.writestr("notes.pdf", pdf_bytes)

    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=notes.zip"}
    )
