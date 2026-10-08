from __future__ import annotations

import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .exports import make_excel, make_pdf
from .learning import find_exact_case, parse_finished_workbook, replace_verified_case, save_verified_case, training_stats, verified_guidance
from .recognition import MODEL, combine_pages, extract_page, prepare_image
from .schema import PackingList


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
KEY_FILE = ROOT / ".local" / "gemini_api_key"
MAX_FILE_BYTES = 18 * 1024 * 1024
MAX_PAGES = 50
app = FastAPI(title="Packing List Studio")
executor = ThreadPoolExecutor(max_workers=1)
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def get_key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"].strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    return ""


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/static/{filename}")
def static_file(filename: str) -> FileResponse:
    if filename not in {"app.js", "style.css"}:
        raise HTTPException(404)
    return FileResponse(STATIC / filename)


@app.get("/api/status")
def status() -> dict:
    return {"key_configured": bool(get_key()), "model": MODEL, **training_stats()}


class KeyInput(BaseModel):
    key: str = Field(min_length=15, max_length=500)


@app.post("/api/settings/key")
def save_key(body: KeyInput) -> dict:
    key = body.key.strip()
    if not key:
        raise HTTPException(422, "API key is empty")
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key, encoding="utf-8")
    return {"key_configured": True}


def _upload_to_pages(filename: str, raw: bytes) -> list[bytes]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        document = pymupdf.open(stream=raw, filetype="pdf")
        if len(document) > MAX_PAGES:
            raise ValueError(f"PDF has more than {MAX_PAGES} pages")
        pages = []
        for page in document:
            pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            pages.append(prepare_image(pixels.tobytes("png")))
        document.close()
        return pages
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError(f"Unsupported file type: {suffix or 'unknown'}")
    return [prepare_image(raw)]


def _run_job(job_id: str, pages: list[bytes], key: str) -> None:
    extracted = []
    try:
        guidance = verified_guidance()
        for index, image in enumerate(pages, start=1):
            page_guidance = guidance
            if extracted and extracted[-1].boxes:
                last_box = extracted[-1].boxes[-1].number
                page_guidance += f"\nPAGE CONTINUATION: The previous uploaded photo ended with Box {last_box}. Any active handwritten item lines above the FIRST new box heading on this photo continue Box {last_box}; include them under that box number.\n"
            page = extract_page(image, index, key, page_guidance)
            extracted.append(page)
            with jobs_lock:
                jobs[job_id]["completed"] = index
        result = combine_pages(extracted)
        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["result"] = result.model_dump()
    except Exception as exc:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(exc)
            if extracted:
                partial = combine_pages(extracted)
                partial.warnings.append("Some uploaded pages were not processed")
                jobs[job_id]["result"] = partial.model_dump()


@app.post("/api/recognize")
async def recognize(files: list[UploadFile] = File(...)) -> dict:
    key = get_key()
    if not files:
        raise HTTPException(400, "Upload at least one photo")
    pages: list[bytes] = []
    for file in files:
        raw = await file.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{file.filename} is larger than 18 MB")
        try:
            pages.extend(_upload_to_pages(file.filename or "", raw))
        except Exception as exc:
            raise HTTPException(400, f"{file.filename}: {exc}") from exc
        if len(pages) > MAX_PAGES:
            raise HTTPException(400, f"Maximum {MAX_PAGES} pages per job")
    job_id = uuid.uuid4().hex
    verified = find_exact_case(pages)
    if verified:
        case_id, result = verified
        with jobs_lock:
            jobs[job_id] = {"status": "done", "completed": len(pages), "total": len(pages), "result": result.model_dump(), "error": None, "pages": pages, "origin": "reviewed_recognition", "finalized_case_id": case_id, "cache_hit": True}
        return {"job_id": job_id, "total": len(pages)}
    if not key:
        raise HTTPException(400, "Save a Gemini API key in Settings first")
    with jobs_lock:
        jobs[job_id] = {"status": "running", "completed": 0, "total": len(pages), "result": None, "error": None, "pages": pages, "origin": "reviewed_recognition", "finalized_case_id": None}
    executor.submit(_run_job, job_id, pages, key)
    return {"job_id": job_id, "total": len(pages)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(404, "Job not found")
        return {key: value for key, value in jobs[job_id].items() if key != "pages"}


@app.get("/api/jobs/{job_id}/pages/{index}")
def job_page(job_id: str, index: int) -> Response:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None or index < 0 or index >= len(job["pages"]):
            raise HTTPException(404, "Page not found")
        image = job["pages"][index]
    return Response(image, media_type="image/jpeg")


@app.post("/api/training/import")
async def import_finished_example(sheet: UploadFile = File(...), photos: list[UploadFile] = File(...)) -> dict:
    if not sheet.filename or Path(sheet.filename).suffix.lower() != ".xlsx":
        raise HTTPException(400, "Choose a finished .xlsx packing list")
    raw_sheet = await sheet.read(MAX_FILE_BYTES + 1)
    if len(raw_sheet) > MAX_FILE_BYTES:
        raise HTTPException(413, "Excel file is larger than 18 MB")
    pages: list[bytes] = []
    for photo in photos:
        raw = await photo.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{photo.filename} is larger than 18 MB")
        try:
            pages.extend(_upload_to_pages(photo.filename or "", raw))
        except Exception as exc:
            raise HTTPException(400, f"{photo.filename}: {exc}") from exc
    if not pages or len(pages) > MAX_PAGES:
        raise HTTPException(400, f"Choose 1 to {MAX_PAGES} photos or PDF pages")
    try:
        data = parse_finished_workbook(raw_sheet, sheet.filename)
    except Exception as exc:
        raise HTTPException(400, f"Could not read finished Excel: {exc}") from exc
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "done", "completed": len(pages), "total": len(pages), "result": data.model_dump(), "error": None, "pages": pages, "origin": "manual_import", "finalized_case_id": None}
    return {"job_id": job_id, "total": len(pages), "result": data.model_dump()}


class FinalInput(BaseModel):
    job_id: str
    data: PackingList


@app.post("/api/finalize")
def finalize(body: FinalInput) -> dict:
    with jobs_lock:
        job = jobs.get(body.job_id)
        if not job:
            raise HTTPException(404, "The source job was not found. Upload the photos again.")
        if job["status"] not in {"done", "error"} or not job.get("result"):
            raise HTTPException(400, "Finish processing before confirming the list")
        try:
            if job["finalized_case_id"]:
                case_id = job["finalized_case_id"]
                replace_verified_case(case_id, body.data)
            else:
                case_id = save_verified_case(body.data, job["pages"], job["origin"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        job["finalized_case_id"] = case_id
    return {"case_id": case_id, **training_stats()}


def _safe_filename(data: PackingList) -> str:
    raw = f"{data.customer or 'PACKING LIST'} {data.packing_date}".strip()
    return re.sub(r"[^A-Za-z0-9 _-]+", "", raw).strip()[:80] or "PACKING LIST"


@app.post("/api/export/{kind}")
def export(kind: str, data: PackingList) -> Response:
    try:
        if kind == "xlsx":
            payload = make_excel(data)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif kind == "pdf":
            payload = make_pdf(data)
            media = "application/pdf"
        else:
            raise HTTPException(404)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    filename = f"{_safe_filename(data)}.{kind}"
    return Response(payload, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

