"""Capa de acceso a datos.

Hay dos implementaciones del mismo contrato:

* AstraRepository  habla CQL contra Astra DB (Cassandra gestionado).
* MemoryRepository guarda todo en diccionarios.

La segunda existe para que las pruebas y el arranque en frio no dependan
de la red ni de credenciales: el workflow de CI corre la suite completa
sin tocar Astra, y la app levanta igual si todavia no configuraste el
bundle. Ambas respetan el mismo orden de resultados que impone el
CLUSTERING ORDER del esquema.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Protocol

from app.config import Settings
from app.models import Click, Link

logger = logging.getLogger(__name__)

ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 7


def generate_code() -> str:
    """Codigo corto aleatorio.

    Se usa azar y no un contador autoincremental porque en Cassandra no
    existe tal cosa: no hay un nodo coordinador que reparta secuencias.
    El alfabeto omite caracteres ambiguos (l, I, 1, O, 0).
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Repository(Protocol):
    def create_link(self, code: str, target_url: str, owner: str) -> Link: ...
    def get_link(self, code: str) -> Link | None: ...
    def list_links_by_owner(self, owner: str, limit: int = 20) -> list[Link]: ...
    def record_click(self, code: str, referrer: str, user_agent: str) -> None: ...
    def recent_clicks(self, code: str, limit: int = 25) -> list[Click]: ...
    def total_clicks(self, code: str) -> int: ...


class MemoryRepository:
    """Implementacion en memoria. No persiste nada entre reinicios."""

    backend = "memoria"

    def __init__(self) -> None:
        self._links: dict[str, Link] = {}
        self._clicks: dict[str, list[Click]] = {}
        self._counts: dict[str, int] = {}

    def create_link(self, code: str, target_url: str, owner: str) -> Link:
        link = Link(code=code, target_url=target_url, owner=owner, created_at=_utcnow())
        self._links[code] = link
        return link

    def get_link(self, code: str) -> Link | None:
        return self._links.get(code)

    def list_links_by_owner(self, owner: str, limit: int = 20) -> list[Link]:
        found = [lk for lk in self._links.values() if lk.owner == owner]
        found.sort(key=lambda lk: (lk.created_at, lk.code), reverse=True)
        return found[:limit]

    def record_click(self, code: str, referrer: str, user_agent: str) -> None:
        click = Click(
            code=code,
            clicked_at=_utcnow(),
            referrer=referrer,
            user_agent=user_agent,
        )
        self._clicks.setdefault(code, []).append(click)
        self._counts[code] = self._counts.get(code, 0) + 1

    def recent_clicks(self, code: str, limit: int = 25) -> list[Click]:
        found = sorted(
            self._clicks.get(code, []), key=lambda c: c.clicked_at, reverse=True
        )
        return found[:limit]

    def total_clicks(self, code: str) -> int:
        return self._counts.get(code, 0)


class AstraRepository:
    """Implementacion CQL.

    Una consulta por tabla, siempre con la clave de particion en el
    WHERE: nunca hay ALLOW FILTERING.
    """

    backend = "astra"

    def __init__(self, session, keyspace: str) -> None:
        self._session = session
        self._keyspace = keyspace
        # Las sentencias preparadas se compilan una sola vez y viajan por
        # la red como un id mas parametros. Ademas el driver puede enrutar
        # la peticion directo al nodo duenio de la particion.
        self._insert_link_by_code = session.prepare(
            "INSERT INTO links_by_code (code, target_url, owner, created_at) "
            "VALUES (?, ?, ?, ?)"
        )
        self._insert_link_by_owner = session.prepare(
            "INSERT INTO links_by_owner (owner, created_at, code, target_url) "
            "VALUES (?, ?, ?, ?)"
        )
        self._select_link = session.prepare(
            "SELECT code, target_url, owner, created_at FROM links_by_code "
            "WHERE code = ?"
        )
        self._select_by_owner = session.prepare(
            "SELECT owner, created_at, code, target_url FROM links_by_owner "
            "WHERE owner = ? LIMIT ?"
        )
        self._insert_click = session.prepare(
            "INSERT INTO clicks_by_code "
            "(code, clicked_at, click_id, referrer, user_agent) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        self._select_clicks = session.prepare(
            "SELECT code, clicked_at, referrer, user_agent FROM clicks_by_code "
            "WHERE code = ? LIMIT ?"
        )
        self._bump_counter = session.prepare(
            "UPDATE click_counts SET total = total + 1 WHERE code = ?"
        )
        self._select_counter = session.prepare(
            "SELECT total FROM click_counts WHERE code = ?"
        )

    def create_link(self, code: str, target_url: str, owner: str) -> Link:
        created_at = _utcnow()
        # Dos escrituras deliberadas: el mismo enlace vive en dos tablas.
        # En Cassandra escribir es barato y leer mal modelado es caro, asi
        # que se duplica al escribir para que cada lectura toque una sola
        # particion. No hay transaccion: son dos INSERT idempotentes.
        self._session.execute(
            self._insert_link_by_code, (code, target_url, owner, created_at)
        )
        self._session.execute(
            self._insert_link_by_owner, (owner, created_at, code, target_url)
        )
        return Link(
            code=code, target_url=target_url, owner=owner, created_at=created_at
        )

    def get_link(self, code: str) -> Link | None:
        row = self._session.execute(self._select_link, (code,)).one()
        if row is None:
            return None
        return Link(
            code=row.code,
            target_url=row.target_url,
            owner=row.owner,
            created_at=row.created_at,
        )

    def list_links_by_owner(self, owner: str, limit: int = 20) -> list[Link]:
        rows = self._session.execute(self._select_by_owner, (owner, limit))
        # Ya vienen ordenados por created_at DESC gracias al CLUSTERING
        # ORDER BY: Cassandra los lee en ese orden desde el SSTable.
        return [
            Link(
                code=r.code,
                target_url=r.target_url,
                owner=r.owner,
                created_at=r.created_at,
            )
            for r in rows
        ]

    def record_click(self, code: str, referrer: str, user_agent: str) -> None:
        self._session.execute(
            self._insert_click,
            (code, _utcnow(), uuid.uuid4(), referrer, user_agent),
        )
        self._session.execute(self._bump_counter, (code,))

    def recent_clicks(self, code: str, limit: int = 25) -> list[Click]:
        rows = self._session.execute(self._select_clicks, (code, limit))
        return [
            Click(
                code=r.code,
                clicked_at=r.clicked_at,
                referrer=r.referrer or "",
                user_agent=r.user_agent or "",
            )
            for r in rows
        ]

    def total_clicks(self, code: str) -> int:
        row = self._session.execute(self._select_counter, (code,)).one()
        return int(row.total) if row and row.total is not None else 0


def resolve_bundle_path(settings: Settings) -> str:
    """Devuelve la ruta al Secure Connect Bundle.

    En local es un archivo en disco. En Render y en Actions no se pueden
    subir binarios como secret, asi que el bundle viaja en base64 y aqui
    se materializa en un archivo temporal.
    """
    if settings.astra_scb_path and os.path.exists(settings.astra_scb_path):
        return settings.astra_scb_path

    if settings.astra_scb_b64:
        raw = base64.b64decode(settings.astra_scb_b64, validate=True)
        handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        handle.write(raw)
        handle.close()
        return handle.name

    raise FileNotFoundError(
        "No se encontro el Secure Connect Bundle. Define ASTRA_SCB_PATH "
        "(archivo local) o ASTRA_SCB_B64 (bundle en base64)."
    )


def connect_session(settings: Settings):
    """Abre una sesion CQL contra Astra DB."""
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import Cluster

    cluster = Cluster(
        cloud={"secure_connect_bundle": resolve_bundle_path(settings)},
        auth_provider=PlainTextAuthProvider("token", settings.astra_token),
    )
    return cluster.connect(settings.keyspace)


def build_repository(settings: Settings) -> Repository:
    """Crea el repositorio adecuado segun lo que haya configurado.

    Si Astra no esta disponible se degrada a memoria en lugar de tumbar
    el proceso: la app sigue respondiendo y /health reporta el backend
    real, que es lo que conviene ver en el pipeline.
    """
    if not settings.configured:
        logger.warning("Astra no configurado; usando repositorio en memoria.")
        return MemoryRepository()

    try:
        session = connect_session(settings)
        logger.info("Conectado a Astra DB, keyspace=%s", settings.keyspace)
        return AstraRepository(session, settings.keyspace)
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudo conectar a Astra (%s); usando memoria.", exc)
        return MemoryRepository()
