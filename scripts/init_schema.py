"""Crea las tablas del keyspace a partir de app/schema.cql.

Se ejecuta una sola vez, a mano, antes del primer despliegue:

    python scripts/init_schema.py

Astra ya trae el keyspace creado desde el portal, asi que aqui solo se
aplican los CREATE TABLE IF NOT EXISTS: el script es idempotente y se
puede volver a correr sin romper nada.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import connect_session  # noqa: E402

SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "app" / "schema.cql"


def sentencias(texto: str) -> list[str]:
    """Separa el archivo en sentencias, ignorando comentarios y vacios."""
    limpio = "\n".join(
        linea for linea in texto.splitlines() if not linea.strip().startswith("--")
    )
    return [bloque.strip() for bloque in limpio.split(";") if bloque.strip()]


def main() -> int:
    settings = get_settings()
    if not settings.configured:
        print("ERROR: falta ASTRA_DB_TOKEN y/o el Secure Connect Bundle.")
        print("Copia .env.example a .env y completalo.")
        return 1

    session = connect_session(settings)
    print(f"Conectado al keyspace '{settings.keyspace}'.")

    for sentencia in sentencias(SCHEMA.read_text(encoding="utf-8")):
        nombre = sentencia.split("IF NOT EXISTS")[-1].split("(")[0].strip()
        session.execute(sentencia)
        print(f"  tabla lista: {nombre}")

    print("Esquema aplicado correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
