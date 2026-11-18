from __future__ import annotations

import importlib
import logging
import os
import re
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
DOWNLOAD_DIR = DATA_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_COOKIE = "schoolbook_session"
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", str(8 * 60 * 60)))
DOWNLOAD_WORKERS = max(1, min(6, int(os.getenv("DOWNLOAD_WORKERS", "2"))))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("schoolbook-web")

# Keep these aligned with the service adapters in utils.py.
PUBLISHERS: dict[str, dict[str, Any]] = {
    "bsm": {"name": "bSmart", "credentials": True},
    "ees": {"name": "easyeschool", "credentials": True},
    "hbs": {"name": "Mondadori HUB Scuola", "credentials": True},
    "mcm": {"name": "MEE2", "credentials": True},
    "myl": {"name": "MyLim", "credentials": True},
    "prs": {"name": "Pearson Reader+ / Pearson+", "credentials": True},
    "dbk": {"name": "Laterza diBooK", "credentials": True},
    "olb": {"name": "Oxford Learner’s Bookshelf", "credentials": True},
    "rfl": {"name": "Raffaello Player", "credentials": True},
    "cmb": {"name": "Cambridge GO", "credentials": True},
    "blk": {"name": "Blinklearning", "credentials": True},
    "hoe": {"name": "HoepliAcademy+", "credentials": True},
    "pmb": {"name": "Palumbo Editore – Saggi Digitali", "credentials": False},
    "cng": {"name": "Cengage Read", "credentials": True},
    "orc": {"name": "Oxford Reading Club", "credentials": True},
    "snm": {"name": "Sanoma My Place", "credentials": True},
}

@dataclass
class UserSession:
    service: str
    token: str
    books: dict[str, dict[str, Any]]
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

SESSIONS: dict[str, UserSession] = {}
JOBS: dict[str, dict[str, Any]] = {}
STATE_LOCK = threading.RLock()
EXECUTOR = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS, thread_name_prefix="book-download")

app = FastAPI(title="Schoolbook Studio", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class LoginPayload(BaseModel):
    service: str
    username: str = ""
    password: str = ""


class DownloadPayload(BaseModel):
    book_ids: list[str] = Field(min_length=1, max_length=200)


def get_service_module(service_code: str):
    if service_code not in PUBLISHERS:
        raise ValueError("Editore non supportato")
    return importlib.import_module(f"services.{service_code}")


def get_user_session(request: Request) -> tuple[str, UserSession]:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise HTTPException(status_code=401, detail="Sessione terminata. Accedi di nuovo.")
    with STATE_LOCK:
        session = SESSIONS.get(sid)
        if not session or time.time() - session.last_seen > SESSION_TTL:
            SESSIONS.pop(sid, None)
            raise HTTPException(status_code=401, detail="Sessione terminata. Accedi di nuovo.")
        session.last_seen = time.time()
        return sid, session


def safe_error(exc: Exception) -> str:
    # Raffaello's adapter raises intentionally redacted, user-facing diagnostics.
    if exc.__class__.__name__ == "RaffaelloAPIError":
        return str(exc)[:450]
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "Il servizio non risponde. Controlla la connessione e riprova."
    return f"Operazione non riuscita ({exc.__class__.__name__}). Verifica l’accesso e riprova."


def _public_books(raw_books: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_books, dict):
        raise ValueError("Il servizio non ha restituito una libreria nel formato previsto")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_id, raw_data in raw_books.items():
        book_id = str(raw_id)
        if isinstance(raw_data, dict):
            title = str(raw_data.get("title") or raw_data.get("name") or f"Libro {book_id}")
            data = raw_data
        else:
            title = str(raw_data or f"Libro {book_id}")
            data = {"title": title}
        # Keep the full provider payload server-side; only title and id reach the browser.
        normalized[book_id] = {"title": title, "data": data}
    return normalized


def _progress(job_id: str, percent: Any, status: str = "Download in corso") -> None:
    try:
        amount = max(0, min(99, int(float(percent))))
    except (TypeError, ValueError):
        amount = 0
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job and job["status"] == "running":
            job["progress"] = amount
            if status:
                job["message"] = str(status)[:90]
            job["updated_at"] = time.time()


def _download_worker(sid: str, job_id: str, service_code: str, token: str,
                     book_id: str, book_data: dict[str, Any]) -> None:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["message"] = "Preparazione download"
        job["updated_at"] = time.time()

    pdf = None
    temp_path: Path | None = None
    try:
        module = get_service_module(service_code)
        pdf = module.downloadbook(
            token,
            book_id,
            book_data,
            lambda percent, status="": _progress(job_id, percent, status),
        )
        if pdf is None or not hasattr(pdf, "save"):
            raise TypeError("Il servizio non ha restituito un documento PDF")

        title = str(book_data.get("title", f"Libro {book_id}"))
        safe_title = re.sub(r"[^\w.-]+", "-", title, flags=re.UNICODE).strip("-._")[:110] or "libro"
        safe_id = re.sub(r"[^\w.-]+", "-", book_id, flags=re.UNICODE).strip("-._")[:40] or "book"
        folder = DOWNLOAD_DIR / service_code
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{safe_id}-{safe_title}-{job_id[:6]}.pdf"
        final_path = folder / filename
        temp_path = folder / f".{job_id}.partial.pdf"

        with STATE_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["message"] = "Salvataggio PDF"
                JOBS[job_id]["progress"] = 98
                JOBS[job_id]["updated_at"] = time.time()

        now = fitz.get_pdf_now()
        pdf.set_metadata({
            "producer": "PyMuPDF " + fitz.version[0],
            "format": "PDF 1.7",
            "encryption": None,
            "author": "",
            "modDate": now,
            "keywords": "",
            "title": title,
            "creationDate": now,
            "creator": "Schoolbook Studio",
            "subject": "",
        })
        pdf.save(str(temp_path), garbage=2, clean=True, deflate=True)
        os.replace(temp_path, final_path)

        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update({
                    "status": "completed",
                    "progress": 100,
                    "message": "PDF pronto",
                    "file_name": filename,
                    "file_size": final_path.stat().st_size,
                    "updated_at": time.time(),
                })
    except Exception as exc:
        logger.warning("Book download failed: service=%s error_type=%s", service_code, type(exc).__name__)
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update({
                    "status": "failed",
                    "progress": 0,
                    "message": safe_error(exc),
                    "updated_at": time.time(),
                })
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    finally:
        if pdf is not None and hasattr(pdf, "close"):
            try:
                pdf.close()
            except Exception:
                pass
        # Do not keep provider tokens or the full catalogue payload in job history.
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.pop("token", None)
                job.pop("book_data", None)
                job.pop("book_id", None)


def _job_for_client(job: dict[str, Any]) -> dict[str, Any]:
    return {key: job.get(key) for key in (
        "id", "service", "title", "status", "progress", "message", "file_name", "file_size"
    )}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/publishers")
def publishers():
    return [
        {"code": code, "name": value["name"], "requires_credentials": value["credentials"]}
        for code, value in PUBLISHERS.items()
    ]


@app.post("/api/login")
def login(payload: LoginPayload, request: Request, response: Response):
    if payload.service not in PUBLISHERS:
        raise HTTPException(status_code=400, detail="Seleziona un editore valido.")
    publisher = PUBLISHERS[payload.service]
    if publisher["credentials"] and (not payload.username.strip() or not payload.password):
        raise HTTPException(status_code=422, detail="Inserisci email/username e password.")

    try:
        module = get_service_module(payload.service)
        # Palumbo's source adapter exposes a public catalogue and intentionally uses a dummy token.
        token = module.login(payload.username.strip(), payload.password) if publisher["credentials"] else module.login("", "")
        if not token:
            raise PermissionError("Accesso non riuscito")
        if hasattr(module, "checktoken") and not module.checktoken(token):
            raise PermissionError("Credenziali non valide o sessione non accettata")
        books = _public_books(module.library(token))
    except Exception as exc:
        logger.warning("Publisher login/library failed: service=%s error_type=%s", payload.service, type(exc).__name__)
        raise HTTPException(status_code=401, detail=safe_error(exc)) from None

    sid = secrets.token_urlsafe(32)
    with STATE_LOCK:
        # Prune expired in-memory sessions and remove the previous browser session.
        now = time.time()
        expired = [key for key, value in SESSIONS.items() if now - value.last_seen > SESSION_TTL]
        for key in expired:
            SESSIONS.pop(key, None)
        old_sid = request.cookies.get(SESSION_COOKIE)
        if old_sid:
            SESSIONS.pop(old_sid, None)
        SESSIONS[sid] = UserSession(service=payload.service, token=str(token), books=books)

    response.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=SESSION_TTL,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "0") == "1",
        samesite="strict",
        path="/",
    )
    return {
        "publisher": publisher["name"],
        "count": len(books),
        "books": [{"id": book_id, "title": book["title"]} for book_id, book in books.items()],
    }


@app.get("/api/books")
def books(request: Request):
    _, session = get_user_session(request)
    publisher = PUBLISHERS[session.service]
    return {
        "publisher": publisher["name"],
        "count": len(session.books),
        "books": [{"id": book_id, "title": book["title"]} for book_id, book in session.books.items()],
    }


@app.post("/api/downloads")
def start_downloads(payload: DownloadPayload, request: Request):
    sid, session = get_user_session(request)
    unique_ids = list(dict.fromkeys(str(book_id) for book_id in payload.book_ids))
    missing = [book_id for book_id in unique_ids if book_id not in session.books]
    if missing:
        raise HTTPException(status_code=400, detail="Uno o più libri non appartengono alla libreria selezionata.")

    created = []
    dispatch = []
    with STATE_LOCK:
        for book_id in unique_ids:
            job_id = uuid.uuid4().hex
            book = session.books[book_id]
            JOBS[job_id] = {
                "id": job_id,
                "owner": sid,
                "service": session.service,
                "title": book["title"],
                "status": "queued",
                "progress": 0,
                "message": "In coda",
                "updated_at": time.time(),
            }
            dispatch.append((sid, job_id, session.service, session.token, book_id, book["data"]))
            created.append(job_id)

    for args in dispatch:
        EXECUTOR.submit(_download_worker, *args)
    return {"jobs": created, "queued": len(created), "parallel_workers": DOWNLOAD_WORKERS}


@app.get("/api/jobs")
def list_jobs(request: Request):
    sid, _ = get_user_session(request)
    with STATE_LOCK:
        own = [job for job in JOBS.values() if job.get("owner") == sid]
        own.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return [_job_for_client(job) for job in own[:60]]


@app.get("/api/jobs/{job_id}/file")
def download_file(job_id: str, request: Request):
    sid, _ = get_user_session(request)
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("owner") != sid:
            raise HTTPException(status_code=404, detail="File non trovato.")
        if job.get("status") != "completed" or not job.get("file_name"):
            raise HTTPException(status_code=409, detail="Il PDF non è ancora pronto.")
        file_path = DOWNLOAD_DIR / job["service"] / job["file_name"]
        title = job["title"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Il PDF non è più disponibile.")
    return FileResponse(file_path, media_type="application/pdf", filename=f"{title}.pdf")


@app.post("/api/logout")
def logout(request: Request, response: Response):
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        with STATE_LOCK:
            SESSIONS.pop(sid, None)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}
