import os
import io
import re
import zipfile
from typing import List, Tuple

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

# File handling
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

# Optional extractors
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from pptx import Presentation
except Exception:
    Presentation = None

# OpenAI
from openai import OpenAI


# =====================
# App + Config
# =====================

app = FastAPI()

MODEL = os.getenv("MODEL", "gpt-4.1")  # MOST POWERFUL API MODEL
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

MAX_FILES = int(os.getenv("MAX_FILES", "10"))
MAX_MB_PER_FILE = int(os.getenv("MAX_MB_PER_FILE", "25"))
MAX_TOTAL_MB = int(os.getenv("MAX_TOTAL_MB", "80"))

SHARED_SECRET = os.getenv("BACKEND_SHARED_SECRET", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)


# =====================
# Utilities
# =====================

@app.get("/health")
def health():
    return {"ok": True}


def _require_secret(request: Request):
    if not SHARED_SECRET:
        return
    if request.headers.get("X-Backend-Secret") != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _safe_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name or "file")[:120]


def _read_uploadfile(f: UploadFile) -> bytes:
    data = f.file.read()
    if len(data) > MAX_MB_PER_FILE * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"{f.filename} too large")
    return data


def _check_total_size(files: List[Tuple[str, bytes]]):
    total = sum(len(b) for _, b in files)
    if total > MAX_TOTAL_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Total upload too large")


# =====================
# Text Extraction
# =====================

def extract_pdf(data: bytes) -> str:
    if PdfReader is None:
        raise HTTPException(status_code=500, detail="pypdf not installed")
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(
        (page.extract_text() or "").strip()
        for page in reader.pages
        if page.extract_text()
    )


def extract_docx(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())


def extract_pptx(data: bytes) -> str:
    if Presentation is None:
        raise HTTPException(status_code=500, detail="python-pptx not installed")
    prs = Presentation(io.BytesIO(data))
    out = []
    for i, slide in enumerate(prs.slides, start=1):
        parts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                parts.append(shape.text.strip())
        try:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"(Notes) {notes}")
        except Exception:
            pass
        if parts:
            out.append(f"Slide {i}:\n" + "\n".join(parts))
    return "\n\n".join(out)


def extract_text(filename: str, data: bytes) -> str:
    fn = filename.lower()
    if fn.endswith(".pdf"):
        return extract_pdf(data)
    if fn.endswith(".docx"):
        return extract_docx(data)
    if fn.endswith(".pptx"):
        return extract_pptx(data)
    if fn.endswith(".txt") or fn.endswith(".md"):
        return data.decode("utf-8", errors="ignore").strip()
    raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")


# =====================
# AI — FINAL PROMPT (GPT-4.1)
# =====================

def call_ai_make_notes(source_text: str) -> str:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY missing")

    system = (
        "You are a senior pharmacy educator creating exam-critical study material. "
        "You MUST strictly use ONLY the provided source material. "
        "You are NOT allowed to add external knowledge, assumptions, or general facts. "
        "If something is not explicitly stated in the source, you MUST write exactly: "
        "'Not covered in these slides'. "
        "Output must be clean, strict Markdown suitable for DOCX and PDF conversion."
    )

    user = (
        "You MUST create TWO SEPARATE DOCUMENTS in ONE response.\n\n"

        "# DOCUMENT 1: STUDY NOTES (PHARMACY)\n\n"

        "Audience:\n"
        "- A pharmacy student studying this topic for the FIRST TIME.\n\n"

        "MANDATORY RULES:\n"
        "- Do NOT remove or omit anything related to the learning objectives.\n"
        "- Organize core content BY learning objective.\n"
        "- Be concise, consolidated, and accurate.\n"
        "- Do NOT invent or infer beyond the source.\n"
        "- Any missing detail must be written as: 'Not covered in these slides'.\n\n"

        "MANDATORY STRUCTURE:\n"
        "## Title\n"
        "## Learning Objectives\n"
        "## Big Picture Overview (5–6 bullets explaining how concepts connect)\n"
        "## Core Notes (grouped explicitly by learning objective)\n"
        "## Diagrams & Flowcharts\n"
        "   - You MUST include text-based diagrams or flowcharts WHERE THEY HELP UNDERSTANDING.\n"
        "   - Especially for mechanisms, pathways, comparisons, or cause–effect relationships.\n"
        "   - Use ASCII arrows, steps, or simple box flows.\n"
        "## Additional Information\n"
        "   - ONLY content not essential for meeting learning objectives.\n"
        "## Key Terms (clear, pharmacy-relevant definitions)\n\n"

        "# DOCUMENT 2: RAPID REVIEW (PHARMACY EXAM)\n\n"

        "Audience:\n"
        "- A student revising immediately before an exam.\n\n"

        "MANDATORY RULES:\n"
        "- Extremely concise.\n"
        "- Bullet points ONLY.\n"
        "- No explanations unless absolutely essential.\n"
        "- Focus on high-yield exam recall.\n\n"

        "MANDATORY STRUCTURE:\n"
        "## High-Yield Facts\n"
        "## High-Yield Drug Points (MOA, indication, cautions, interactions IF PRESENT)\n"
        "## Interactions & Monitoring\n"
        "## Common Exam Traps / Confusions\n"
        "## Exam-Style Questions\n\n"

        "================================\n"
        "SOURCE MATERIAL (USE ONLY THIS)\n"
        "================================\n"
        f"{source_text}"
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
    )

    content = response.choices[0].message.content.strip()
    if not content:
        raise HTTPException(status_code=500, detail="AI returned empty content")
    return content


# =====================
# Output Builders
# =====================

def markdown_to_docx(md: str) -> bytes:
    doc = Document()
    for line in md.splitlines():
        if not line.strip():
            continue
        if line.startswith("### "):
            doc.add_heading(line[4:], level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=2)
        elif line.startswith("# "):
            doc.add_heading(line[2:], level=1)
        elif line.lstrip().startswith(("-", "*")):
            doc.add_paragraph(line.lstrip()[1:].strip(), style="List Bullet")
        else:
            doc.add_paragraph(line.strip())
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def markdown_to_pdf(md: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x, y = 2 * cm, height - 2 * cm

    def draw(text, bold=False):
        nonlocal y
        if y < 2 * cm:
            c.showPage()
            y = height - 2 * cm
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11)
        for i in range(0, len(text), 90):
            c.drawString(x, y, text[i:i+90])
            y -= 14

    for line in md.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            draw(line.lstrip("# ").strip(), bold=True)
        elif line.lstrip().startswith(("-", "*")):
            draw("• " + line.lstrip()[1:].strip())
        else:
            draw(line.strip())

    c.save()
    return buf.getvalue()


def build_zip(docx: bytes, pdf: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("notes.docx", docx)
        z.writestr("notes.pdf", pdf)
    return buf.getvalue()


# =====================
# Endpoint
# =====================

@app.post("/make-notes")
async def make_notes(request: Request, files: List[UploadFile] = File(...)):
    _require_secret(request)

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail="Too many files")

    file_blobs = []
    for f in files:
        data = _read_uploadfile(f)
        file_blobs.append((_safe_filename(f.filename), data))
    _check_total_size(file_blobs)

    extracted = []
    for name, data in file_blobs:
        text = extract_text(name, data)
        extracted.append(f"=== File: {name} ===\n{text}")

    source_text = "\n\n".join(extracted)

    notes_md = call_ai_make_notes(source_text)
    docx = markdown_to_docx(notes_md)
    pdf = markdown_to_pdf(notes_md)

    zip_bytes = build_zip(docx, pdf)

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="notes.zip"'}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
