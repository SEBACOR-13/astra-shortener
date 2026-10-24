"""Pruebas de la API.

Corren contra MemoryRepository, que replica el contrato y el orden de
resultados de las tablas CQL. Asi el pipeline valida la logica sin
necesitar credenciales de Astra ni acceso a la red.
"""

from fastapi.testclient import TestClient

from app.db import ALPHABET, CODE_LENGTH, MemoryRepository, generate_code
from app.main import app

client = TestClient(app)


def test_health_responde_ok():
    respuesta = client.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_codigo_generado_tiene_formato_valido():
    codigo = generate_code()
    assert len(codigo) == CODE_LENGTH
    assert all(caracter in ALPHABET for caracter in codigo)
    # Sin caracteres ambiguos.
    assert not set(codigo) & set("lI1O0")


def test_crear_enlace_devuelve_codigo_y_url_corta():
    respuesta = client.post(
        "/api/links", json={"target_url": "https://upt.edu.pe", "owner": "demo"}
    )
    assert respuesta.status_code == 201

    cuerpo = respuesta.json()
    assert cuerpo["target_url"] == "https://upt.edu.pe"
    assert cuerpo["code"] in cuerpo["short_url"]


def test_url_invalida_es_rechazada():
    for invalida in ["ftp://archivo.zip", "no-es-una-url", ""]:
        respuesta = client.post("/api/links", json={"target_url": invalida})
        assert respuesta.status_code == 422, invalida


def test_redireccion_registra_el_clic():
    creado = client.post(
        "/api/links", json={"target_url": "https://example.com", "owner": "demo"}
    ).json()
    codigo = creado["code"]

    redireccion = client.get(f"/{codigo}", follow_redirects=False)
    assert redireccion.status_code == 302
    assert redireccion.headers["location"] == "https://example.com"

    estadisticas = client.get(f"/api/links/{codigo}/stats").json()
    assert estadisticas["total_clicks"] == 1
    assert len(estadisticas["recent_clicks"]) == 1


def test_contador_acumula_varios_clics():
    codigo = client.post(
        "/api/links", json={"target_url": "https://example.org"}
    ).json()["code"]

    for _ in range(5):
        client.get(f"/{codigo}", follow_redirects=False)

    assert client.get(f"/api/links/{codigo}/stats").json()["total_clicks"] == 5


def test_codigo_inexistente_devuelve_404():
    assert client.get("/api/links/noexiste/stats").status_code == 404
    assert client.get("/zzzzzzz", follow_redirects=False).status_code == 404


def test_pagina_de_inicio_carga():
    respuesta = client.get("/")
    assert respuesta.status_code == 200
    assert "Acortar" in respuesta.text


def test_clics_recientes_salen_del_mas_nuevo_al_mas_viejo():
    """Replica el CLUSTERING ORDER BY (clicked_at DESC) del esquema."""
    repositorio = MemoryRepository()
    repositorio.create_link("abc1234", "https://example.com", "demo")

    for indice in range(3):
        repositorio.record_click("abc1234", referrer=f"ref-{indice}", user_agent="t")

    recientes = repositorio.recent_clicks("abc1234")
    momentos = [click.clicked_at for click in recientes]
    assert momentos == sorted(momentos, reverse=True)


def test_enlaces_por_duenio_no_mezclan_usuarios():
    """La partition key aisla a cada owner en su propia particion."""
    repositorio = MemoryRepository()
    repositorio.create_link("aaa1111", "https://a.com", "sebastian")
    repositorio.create_link("bbb2222", "https://b.com", "fernando")

    mios = repositorio.list_links_by_owner("sebastian")
    assert [enlace.code for enlace in mios] == ["aaa1111"]
