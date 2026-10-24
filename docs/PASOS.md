# Pasos que tienes que hacer tú

El código ya está listo y probado. Lo que queda son las partes que
requieren tus cuentas y tus credenciales — yo no puedo crear cuentas ni
escribir contraseñas por ti. Son unos 30–40 minutos.

Haz los pasos **en este orden**: cada uno produce un dato que el siguiente
necesita.

---

## 1. Astra DB — crear la base de datos (~10 min)

1. Entra a <https://astra.datastax.com> y regístrate (sirve tu cuenta de
   GitHub o Google). No pide tarjeta.
2. **Create Database** → tipo **Serverless (Non-Vector)**.
   - Database name: `shortener-db`
   - Keyspace: `shortener`  ← tiene que llamarse así, o cambia `ASTRA_KEYSPACE`
   - Provider y región: cualquiera del free tier (GCP `us-east1` va bien)
3. Espera a que el estado pase a **Active** (2–3 minutos).

### Token de aplicación

4. En la base de datos → pestaña **Connect** → **Generate Token**
   (rol *Database Administrator*).
5. Copia el token que empieza con `AstraCS:`. **Solo se muestra una vez.**

### Secure Connect Bundle

6. En la misma pestaña **Connect** → **Get Bundle** / *Download Secure
   Connect Bundle*. Baja un archivo `secure-connect-shortener-db.zip`.
7. Muévelo a la carpeta del proyecto (ya está en `.gitignore`, no se sube).

> El enlace de descarga caduca a los 5 minutos, pero el bundle en sí no
> caduca nunca.

---

## 2. Crear las tablas (~5 min)

En la carpeta del proyecto:

```bash
cp .env.example .env
```

Edita `.env` con tus datos reales:

```
ASTRA_DB_TOKEN=AstraCS:lo-que-copiaste
ASTRA_SCB_PATH=./secure-connect-shortener-db.zip
ASTRA_KEYSPACE=shortener
BASE_URL=http://localhost:8000
```

Y aplica el esquema:

```bash
.venv/Scripts/python.exe scripts/init_schema.py
```

Debe imprimir las cuatro tablas. Si falla, el error te dice si es el token
o la ruta del bundle.

Comprueba que la app ya habla con Cassandra:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Abre <http://localhost:8000> — la insignia de arriba a la derecha debe
decir **backend: astra** (antes decía *memoria*). Crea un enlace y ábrelo:
los clics tienen que aparecer. **Este es el momento perfecto para grabar
material del video.**

---

## 3. Subir el repositorio a GitHub (~5 min)

Crea un repositorio **público** vacío en GitHub llamado `astra-shortener`
(sin README, sin .gitignore — ya los tenemos).

Luego, desde la carpeta del proyecto:

```bash
git init -b main
git add .
git commit -m "Acortador de URLs sobre Apache Cassandra con despliegue automatizado"
git remote add origin https://github.com/TU_USUARIO/astra-shortener.git
git push -u origin main
```

> Antes de hacer `git add .`, confirma que el `.zip` del bundle **no**
> aparece en `git status`. Si aparece, algo pasó con el `.gitignore`.

El pipeline se ejecutará y **fallará en el paso de deploy** porque todavía
no existe el secret de Render. Es lo esperado; se arregla en el paso 5.

---

## 4. Render — desplegar (~10 min)

1. Entra a <https://render.com> y regístrate con GitHub. No pide tarjeta.
2. **New** → **Web Service** → conecta tu repositorio `astra-shortener`.
3. Configura:
   - Language / Runtime: **Docker**
   - Plan: **Free**
   - Name: `cortito` (o el que quieras; define tu URL final)
   - **Auto-Deploy: No** ← importante, el deploy lo dispara el pipeline
4. En **Environment Variables**, agrega:

   | Key | Value |
   |---|---|
   | `ASTRA_DB_TOKEN` | tu token `AstraCS:...` |
   | `ASTRA_KEYSPACE` | `shortener` |
   | `ASTRA_SCB_B64` | ver abajo |
   | `BASE_URL` | `https://cortito.onrender.com` |

   Para `ASTRA_SCB_B64`, genera el bundle en base64 — es una sola línea
   larguísima, cópiala entera:

   ```bash
   base64 -w0 secure-connect-shortener-db.zip > bundle.b64
   ```

   (En PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("secure-connect-shortener-db.zip")) | Set-Content bundle.b64`)

   Abre `bundle.b64`, copia todo el contenido y pégalo como valor.
   **Borra `bundle.b64` después**, no lo subas al repo.

5. **Create Web Service**. El primer build tarda unos 3 minutos.
6. Cuando termine, abre la URL y verifica `https://TU-APP.onrender.com/health`
   → debe decir `"backend":"astra"`.

### Copia el Deploy Hook

7. En el servicio → **Settings** → busca **Deploy Hook** → copia esa URL.
   Es secreta: cualquiera con ella puede disparar un despliegue.

---

## 5. Conectar GitHub Actions con Render (~5 min)

En tu repositorio de GitHub → **Settings** → **Secrets and variables** →
**Actions**:

- Pestaña **Secrets** → *New repository secret*:
  - Name: `RENDER_DEPLOY_HOOK_URL`
  - Value: la URL del deploy hook del paso anterior

- Pestaña **Variables** → *New repository variable*:
  - Name: `APP_URL`
  - Value: `https://cortito.onrender.com` (sin barra final)

---

## 6. Probar el pipeline completo (~3 min)

Esta es **la prueba que demuestra el requisito de "despliegue mediante
automatización"**, y la mejor toma para el video:

```bash
# cambia algo visible, por ejemplo el título en app/templates/index.html
git add .
git commit -m "Ajuste del titulo de la portada"
git push
```

Ve a la pestaña **Actions** de GitHub y mira el pipeline: lint → pruebas →
docker build → deploy → verificación de `/health`. Cuando termine en verde,
recarga la app y el cambio está ahí. **Sin tocar ningún panel.**

---

## 7. Publicar el artículo (~10 min)

1. Abre `docs/ARTICULO.md` y reemplaza los dos marcadores:
   - `TU_USUARIO` → tu usuario de GitHub (aparece 2 veces)
   - `https://cortito.onrender.com` → tu URL real de Render (aparece 3 veces)
2. Entra a <https://dev.to> con tu cuenta `sebacor13` → **Create Post**.
3. Pulsa el ícono de ajustes y elige el **editor markdown**; pega el
   contenido completo del archivo, incluido el bloque `---` del principio
   (es el front matter: título, tags y descripción).
4. Cambia `published: false` por `published: true` — o usa el botón
   **Publish** de la interfaz.
5. Opcional pero recomendable: sube una imagen de portada (una captura del
   panel de estadísticas se ve bien).

---

## 8. Entrega

Cuando tengas los tres enlaces, el ZIP para la tarea se arma con un archivo
de texto dentro. Avísame y te lo genero con el formato de la Actividad 1.

- [ ] Artículo en Dev.to
- [ ] Repositorio público en GitHub
- [ ] Aplicación en vivo en Render
- [ ] Video de equipo en YouTube *(pendiente, cuando tu compañero tenga lo suyo)*
- [ ] Enlaces al grupo de Telegram
- [ ] ZIP subido a la tarea

---

## Si algo falla

| Síntoma | Causa probable |
|---|---|
| `/health` dice `"backend":"memoria"` en Render | Falta `ASTRA_SCB_B64` o el token está mal copiado |
| `init_schema.py`: *No se encontro el Secure Connect Bundle* | La ruta de `ASTRA_SCB_PATH` no coincide con el nombre real del `.zip` |
| El pipeline falla en *Desplegar* | Falta el secret `RENDER_DEPLOY_HOOK_URL` |
| El pipeline falla en *Esperar a que el servicio quede sano* | La app tardó más de 5 min, o `APP_URL` está mal escrita |
| La app en Render tarda 30s en cargar | Normal en el plan free: el servicio se duerme tras 15 min sin tráfico |
