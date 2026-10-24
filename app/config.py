"""Configuracion leida del entorno.

En local se toma de un archivo .env; en Render y en GitHub Actions las
mismas variables llegan como secrets, de modo que el codigo no cambia
entre los tres entornos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    astra_token: str
    astra_scb_path: str
    astra_scb_b64: str
    keyspace: str
    base_url: str

    @property
    def configured(self) -> bool:
        """True si hay credenciales suficientes para hablar con Astra."""
        return bool(self.astra_token) and bool(self.astra_scb_path or self.astra_scb_b64)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        astra_token=os.getenv("ASTRA_DB_TOKEN", ""),
        astra_scb_path=os.getenv("ASTRA_SCB_PATH", ""),
        astra_scb_b64=os.getenv("ASTRA_SCB_B64", ""),
        keyspace=os.getenv("ASTRA_KEYSPACE", "shortener"),
        base_url=os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
    )
