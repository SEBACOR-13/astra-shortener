"""API y vistas del acortador.

Las rutas reflejan una a una las consultas del esquema: cada endpoint
toca exactamente una tabla y siempre por su clave de particion.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import get_settings
from app.db import build_repository, generate_code

settings = get_settings()
app = FastAPI(
    title="Acortador de URLs sobre Apache Cassandra",
    description=(
        "Demo de modelado NoSQL orientado a consultas, desplegado de forma "
        "automatica sobre Astra DB y Render."
    ),
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

repo = build_repository(settings)

OWNER_COOKIE = "shortener_owner"
MAX_URL_LENGTH = 2048


class LinkIn(BaseModel):
    target_url: str
    owner: str = "demo"


def _owner_from(request: Request) -> str:
    return request.cookies.get(OWNER_COOKIE) or "anon-" + secrets.token_hex(4)


def _is_valid_url(value: str) -> bool:
    if not value or len(value) > MAX_URL_LENGTH:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@app.get("/health")
def health() -> JSONResponse:
    """Sonda de salud. Render la consulta y el pipeline la verifica."""
    return JSONResponse(
        {
            "status": "ok",
            "backend": getattr(repo, "backend", "desconocido"),
            "keyspace": settings.keyspace,
        }
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    owner = _owner_from(request)
    # Consulta a links_by_owner: una sola particion, ya ordenada.
    links = repo.list_links_by_owner(owner, limit=20)
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "links": links,
            "base_url": settings.base_url,
            "backend": getattr(repo, "backend", "desconocido"),
            "error": request.query_params.get("error"),
        },
    )
    response.set_cookie(OWNER_COOKIE, owner, max_age=60 * 60 * 24 * 365)
    return response


@app.post("/crear")
def crear(request: Request, target_url: str = Form(...)):
    """Alta desde el formulario HTML."""
    target_url = target_url.strip()
    if not _is_valid_url(target_url):
        return RedirectResponse("/?error=url", status_code=303)

    owner = _owner_from(request)
    link = repo.create_link(generate_code(), target_url, owner)
    response = RedirectResponse(f"/s/{link.code}", status_code=303)
    response.set_cookie(OWNER_COOKIE, owner, max_age=60 * 60 * 24 * 365)
    return response


@app.post("/api/links", status_code=201)
def api_crear(payload: LinkIn) -> JSONResponse:
    """Alta por API. Util para demostrar el pipeline con curl."""
    target_url = payload.target_url.strip()
    if not _is_valid_url(target_url):
        return JSONResponse(
            {"detail": "target_url debe ser una URL http(s) valida"}, status_code=422
        )

    link = repo.create_link(generate_code(), target_url, payload.owner)
    return JSONResponse(
        {
            "code": link.code,
            "short_url": f"{settings.base_url}/{link.code}",
            "target_url": link.target_url,
            "owner": link.owner,
            "created_at": link.created_at.isoformat(),
        },
        status_code=201,
    )


@app.get("/api/links/{code}/stats")
def api_stats(code: str) -> JSONResponse:
    link = repo.get_link(code)
    if link is None:
        return JSONResponse({"detail": "codigo no encontrado"}, status_code=404)

    return JSONResponse(
        {
            "code": link.code,
            "target_url": link.target_url,
            "total_clicks": repo.total_clicks(code),
            "recent_clicks": [
                {
                    "clicked_at": c.clicked_at.isoformat(),
                    "referrer": c.referrer,
                    "user_agent": c.user_agent,
                }
                for c in repo.recent_clicks(code, limit=25)
            ],
        }
    )


@app.get("/s/{code}", response_class=HTMLResponse)
def stats(request: Request, code: str):
    """Panel de estadisticas de un enlace."""
    link = repo.get_link(code)
    if link is None:
        return templates.TemplateResponse(
            request=request, name="404.html", context={}, status_code=404
        )

    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={
            "link": link,
            "short_url": f"{settings.base_url}/{link.code}",
            "total": repo.total_clicks(code),
            "clicks": repo.recent_clicks(code, limit=25),
        },
    )


@app.get("/{code}")
def redirigir(request: Request, code: str):
    """Redireccion y registro del clic.

    Esta ruta va al final a proposito: al ser un comodin, capturaria
    cualquier path que se declare despues de ella.
    """
    link = repo.get_link(code)
    if link is None:
        return templates.TemplateResponse(
            request=request, name="404.html", context={}, status_code=404
        )

    repo.record_click(
        code=code,
        referrer=request.headers.get("referer", ""),
        user_agent=request.headers.get("user-agent", "")[:300],
    )
    return RedirectResponse(link.target_url, status_code=302)
