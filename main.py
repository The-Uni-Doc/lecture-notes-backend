import os
import io
import zipfile
from typing import List

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import Response
from docx import Document
from reportlab.pdfgen import canvas
from openai import OpenAI

# ----------------------------
# App + OpenAI client
# ----------------------------

app = FastAPI(title="Lecture Notes Backend", version="1.0")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ----------------------------
# Health check
# ----------------------------

@app.get("/health")
def health():
    return {"ok": True}

# ----------------------------
# Helpers to create files
# ----------------------------

def make_docx(text: str) -> bytes:
    buf = io.BytesIO()
    doc = Document()
    doc.add_heading("Exam Notes", level=1)

    for line in text.split("\n"):
        doc.add_paragraph(line)

    doc.save(buf)
    return buf.getvalue()

def make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)

    y = 800
    for line in text.split("\n"):
        c.drawString(40, y, line[:120])
        y -= 16
        if y < 50:
            c.showPage()
            y = 800

    c.save()
    return buf.getvalue()

# ----------------------------
# MAIN ENDPOINT
# ----------------------------

@app.post("/make-notes")
async def make_notes(files: List[UploadFile] = File(...)):
    filenames = []
    combined_text = ""

    # Read uploaded files (content extraction will be expanded later)
    for f in files:
        data = await f.read()
        filenames.append(f.filename or "lecture file")
        combined_text += f"\nFILE: {f.filename}\n"

    # ----------------------------
    # OpenAI call
    # ----------------------------

    prompt = f"""
You are an expert university study assistant.

Create high-quality, exam-focused notes.
Simplify aggressively but preserve meaning.
Use clear headings and bullet points.

Lecture files:
{", ".join(filenames)}

Content placeholder:
{combined_text}
"""

    response = client.responses.create(
        model="gpt-5.2",
        input=prompt
    )

    notes_text = response.output_text

    # ----------------------------
    # Build outputs
    # ----------------------------

    docx_bytes = make_docx(notes_text)
    pdf_bytes = make_pdf(notes_text)

    # ----------------------------
    # Return ZIP
    # ----------------------------

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("exam_notes.docx", docx_bytes)
        z.writestr("exam_notes.pdf", pdf_bytes)

    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=exam_notes.zip"}
    )
