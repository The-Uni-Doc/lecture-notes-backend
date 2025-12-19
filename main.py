import os
import io
import zipfile
from typing import List

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import Response, JSONResponse
from docx import Document
from reportlab.pdfgen import canvas
from openai import OpenAI

# --- setup ---
app = FastAPI(title="Lecture Notes Backend", version="1.0.0")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.get("/health")
def health():
    return {"ok": True}

# --- helpers ---
def generate_notes_text(filenames: List[str]) -> str:
    prompt = (
        "Create clear, simplified lecture notes from these files:\n"
        + "\n".join(filenames)
        + "\nIf learning objectives exist, focus on them. Otherwise summarize everything."
    )

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
    )

    return resp.choices[0].message.content


def make_docx(text: str) -> bytes:
    doc = Document()
    doc.add_heading("Lecture Notes", level=1)
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in text.split("\n"):
        c.drawString(40, y, line[:110])
        y -= 16
        if y < 40:
            c.showPage()
            y = 800
    c.showPage()
    c.save()
    return buf.getvalue()


# --- main endpoint ---
@app.post("/make-notes")
async def make_notes(files: List[UploadFile] = File(...)):
    if not files:
        return JSONResponse(status_code=400, content={"error": "No files uploaded"})

    filenames = []
    for f in files:
        filenames.append(f.filename)
        await f.read()  # consume upload

    notes_text = generate_notes_text(filenames)

    docx_bytes = make_docx(notes_text)
    pdf_bytes = make_pdf(notes_text)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("notes.docx", docx_bytes)
        z.writestr("notes.pdf", pdf_bytes)

    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=notes.zip"},
    )
