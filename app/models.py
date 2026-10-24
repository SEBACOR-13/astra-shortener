"""Objetos de dominio: lo que la aplicacion maneja, independiente de CQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Link:
    code: str
    target_url: str
    owner: str
    created_at: datetime


@dataclass(frozen=True)
class Click:
    code: str
    clicked_at: datetime
    referrer: str
    user_agent: str
