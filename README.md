# cortito — acortador de URLs sobre Apache Cassandra

Aplicación web que acorta URLs y registra la analítica de cada clic,
construida sobre **Apache Cassandra** (gestionado con **DataStax Astra DB**),
desplegada en **Render** mediante un pipeline de **GitHub Actions**.

> Actividad Grupal 2 — Base de Datos II
> Universidad Privada de Tacna · Ing. Patrick Jose Cuadros Quiroga
> Sebastián Alejandro Cortez Apaza

| | |
|---|---|
| **App en vivo** | `https://cortito.onrender.com` |
| **Base de datos** | Apache Cassandra 4 vía Astra DB (serverless) |
| **Nube** | Render (plan free) |
| **Automatización** | GitHub Actions → deploy hook de Render |

---

## Por qué Cassandra y no un motor relacional

Un acortador es un caso casi de laboratorio para una base de datos
wide-column:

- La lectura dominante es **una sola**: dado un código, devolver la URL.
  Es un acceso por clave, no necesita JOINs ni planificador de consultas.
- La escritura dominante es **masiva y append-only**: cada visita genera
  un registro de clic que nunca se actualiza.
- El tráfico crece de forma horizontal: más enlaces significa más
  particiones, y más particiones se reparten entre más nodos.

Cassandra está diseñada exactamente para esa forma. El precio que cobra
a cambio es que **el modelo de datos se diseña a partir de las consultas**,
no de las entidades.

## El modelo: una tabla por consulta

Este es el núcleo del proyecto. En lugar de una tabla `links` y una
tabla `clicks` normalizadas, hay cuatro tablas, cada una existe para
responder una pregunta:

| Tabla | Pregunta que responde | Clave |
|---|---|---|
| `links_by_code` | ¿A qué URL redirijo este código? | PK simple: `code` |
| `links_by_owner` | ¿Qué enlaces creó este usuario, del más nuevo al más viejo? | partition `owner`, clustering `created_at DESC` |
| `clicks_by_code` | ¿Cuáles fueron los últimos clics de este enlace? | partition `code`, clustering `clicked_at DESC` |
| `click_counts` | ¿Cuántos clics lleva en total? | PK simple + columna `counter` |

Tres decisiones que merecen explicación:

**1. El mismo enlace se escribe dos veces, a propósito.**
`links_by_code` y `links_by_owner` guardan la misma información. En
Cassandra la escritura es barata (va a un commit log secuencial y a una
memtable) y la lectura mal modelada es cara (dispersa por varios nodos).
Por eso se desnormaliza al escribir, para que cada lectura toque una
sola partición.

**2. El orden no se pide, se diseña.**
Ninguna consulta de la app lleva `ORDER BY`. El orden descendente sale
del `CLUSTERING ORDER BY (clicked_at DESC)` declarado en la tabla:
las filas ya están físicamente ordenadas en el SSTable.

**3. Contar filas es caro; para eso están los `counter`.**
Un `SELECT COUNT(*)` recorrería la partición entera. La tabla
`click_counts` usa el tipo `counter`, que resuelve incrementos
concurrentes sin el ciclo leer-modificar-escribir. Como una tabla con
columnas `counter` no admite otras columnas no-clave, vive separada.

El esquema completo y comentado está en [`app/schema.cql`](app/schema.cql).

## Arquitectura

```
Navegador
    |
    v
Render (contenedor Docker, plan free)
    |  FastAPI + driver CQL
    v
Astra DB  --  keyspace "shortener"
              links_by_code | links_by_owner
              clicks_by_code | click_counts
```

## El despliegue es automático

No hay ningún paso manual entre el commit y la aplicación en producción.
El pipeline está en [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml):

```
git push a main
   └─> ruff check          (estilo)
   └─> pytest              (10 pruebas)
   └─> docker build        (la imagen compila)
   └─> POST al deploy hook de Render
   └─> polling a /health hasta que el servicio responda
```

Si cualquier paso falla, el despliegue **no se ejecuta**. Un pull request
pasa por lint y pruebas pero nunca publica.

El servicio en Render está declarado como código en
[`render.yaml`](render.yaml), con `autoDeploy: false` para que el único
camino a producción sea el pipeline.

## Ejecutar en local

```bash
git clone https://github.com/<tu-usuario>/astra-shortener.git
cd astra-shortener

python -m venv .venv
source .venv/Scripts/activate     # en Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env              # completa el token y la ruta del bundle
python scripts/init_schema.py     # crea las cuatro tablas (idempotente)

uvicorn app.main:app --reload
```

Abre <http://localhost:8000>.

Sin credenciales de Astra la aplicación **también arranca**: cae a un
repositorio en memoria y lo informa en `/health`. Eso permite correr la
suite de pruebas en CI sin exponer secretos.

```bash
pytest -v
ruff check app tests scripts
```

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Formulario y lista de mis enlaces |
| `POST` | `/crear` | Alta desde el formulario |
| `POST` | `/api/links` | Alta por API (JSON) |
| `GET` | `/{code}` | Redirección 302 y registro del clic |
| `GET` | `/s/{code}` | Panel de estadísticas |
| `GET` | `/api/links/{code}/stats` | Estadísticas en JSON |
| `GET` | `/health` | Sonda de salud y backend activo |

Ejemplo:

```bash
curl -X POST https://cortito.onrender.com/api/links \
  -H "Content-Type: application/json" \
  -d '{"target_url": "https://www.upt.edu.pe/portal/"}'
```

## Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `ASTRA_DB_TOKEN` | sí | Token de aplicación de Astra (`AstraCS:...`) |
| `ASTRA_SCB_PATH` | en local | Ruta al Secure Connect Bundle `.zip` |
| `ASTRA_SCB_B64` | en la nube | El mismo bundle en base64 (los secrets no admiten binarios) |
| `ASTRA_KEYSPACE` | no | Por defecto `shortener` |
| `BASE_URL` | no | URL pública, para construir los enlaces cortos |

## Estructura

```
app/
  schema.cql      las cuatro tablas, comentadas
  db.py           repositorios: Astra (CQL) y memoria
  main.py         rutas FastAPI
  models.py       objetos de dominio
  templates/      vistas Jinja2
scripts/
  init_schema.py  aplica el esquema
tests/            10 pruebas, sin dependencia de red
.github/workflows/ci-cd.yml
render.yaml       el servicio como código
Dockerfile
```

## Licencia

MIT.
