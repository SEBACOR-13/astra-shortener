---
title: "Una tabla por pregunta: modelar y desplegar sobre Cassandra sin tocar un panel"
published: false
description: "Construí un acortador de URLs sobre Apache Cassandra y lo desplegué con un pipeline donde el único gesto manual es git push. Lo interesante no fue el deploy, fue desaprender el modelo relacional."
tags: cassandra, nosql, devops, python
cover_image: ""
---

Cuando llevas años escribiendo SQL, lo primero que haces frente a una base
de datos nueva es buscar dónde está el `JOIN`. En Apache Cassandra no está,
y ese hueco no es una carencia: es la decisión de diseño de la que cuelga
todo lo demás.

Para entenderlo de verdad construí algo pequeño y lo llevé hasta producción:
un acortador de URLs con analítica de clics, sobre Cassandra gestionado en
Astra DB, desplegado en Render mediante GitHub Actions. El código completo
está en el repositorio, pero lo que quiero contar aquí es el cambio mental
que exige el modelado, porque es la parte que ningún tutorial de "hola
mundo" te obliga a atravesar.

- **Repositorio:** https://github.com/SEBACOR-13/astra-shortener
- **Aplicación en vivo:** https://astra-shortener.onrender.com

---

## Por qué un acortador es el caso perfecto

No lo elegí por original. Lo elegí porque su patrón de acceso es casi una
caricatura de aquello en lo que Cassandra es buena:

- **La lectura dominante es una sola.** Dado un código de siete caracteres,
  devolver una URL. No hay filtros, no hay agregaciones, no hay relaciones
  que recorrer. Es un acceso por clave.
- **La escritura dominante es append-only y masiva.** Cada visita genera un
  registro de clic que nunca se actualiza ni se borra.
- **El crecimiento es horizontal.** Más enlaces significa más particiones,
  y más particiones se reparten entre más nodos sin que nadie reescriba una
  consulta.

Si tu carga de trabajo se parece a esto, Cassandra te da escalado lineal y
alta disponibilidad. Si no se parece —si necesitas consultas analíticas
flexibles sobre columnas arbitrarias— te va a hacer la vida miserable. Esa
es la parte honesta que conviene decir antes de empezar.

## El giro: no modelas entidades, modelas preguntas

En el modelo relacional diseñas las tablas una vez, normalizas, y después
escribes las consultas que necesites. El motor se encarga de resolverlas.

En Cassandra el orden está invertido. Primero escribes la lista de preguntas
que tu aplicación va a hacer, y después creas **una tabla por pregunta**.

Para el acortador, las preguntas eran cuatro:

1. ¿A qué URL redirijo este código corto?
2. ¿Qué enlaces creó este usuario, del más nuevo al más viejo?
3. ¿Cuáles fueron los últimos clics de este enlace?
4. ¿Cuántos clics lleva en total?

Cuatro preguntas, cuatro tablas. Veámoslas, porque cada una enseña algo
distinto.

### Pregunta 1: acceso por clave

```sql
CREATE TABLE links_by_code (
    code        text PRIMARY KEY,
    target_url  text,
    owner       text,
    created_at  timestamp
);
```

La más simple. `code` es la *partition key*: Cassandra calcula su hash,
ese hash determina qué nodo del anillo guarda la fila, y la lectura va
directo a ese nodo. Una partición, un salto de red.

### Pregunta 2: desnormalización deliberada

```sql
CREATE TABLE links_by_owner (
    owner       text,
    created_at  timestamp,
    code        text,
    target_url  text,
    PRIMARY KEY ((owner), created_at, code)
) WITH CLUSTERING ORDER BY (created_at DESC, code ASC);
```

Aquí es donde un desarrollador de SQL hace una mueca: **es la misma
información que la tabla anterior, duplicada**.

Y es exactamente lo correcto. En Cassandra escribir es barato: la escritura
va a un commit log secuencial y a una estructura en memoria, sin leer nada
antes. Leer mal modelado, en cambio, es carísimo, porque obliga al nodo
coordinador a preguntarle a todos los demás. Así que la regla se invierte
respecto a lo que enseñan en Bases de Datos I: **duplicas al escribir para
que cada lectura toque una sola partición**.

Fíjate en la forma de la clave primaria: `((owner), created_at, code)`. El
primer paréntesis interno es la partition key —todos los enlaces de un
usuario viven juntos en el mismo nodo— y lo que sigue son las *clustering
keys*, que determinan el orden **físico** de las filas dentro de esa
partición.

Lo cual lleva al detalle que más me gustó.

### El orden no se pide, se diseña

Esta es la consulta que alimenta la lista de "mis enlaces":

```sql
SELECT owner, created_at, code, target_url
FROM links_by_owner
WHERE owner = ? LIMIT ?
```

No hay `ORDER BY`. Y aun así las filas llegan de la más nueva a la más
vieja, siempre.

El orden sale del `CLUSTERING ORDER BY (created_at DESC)` declarado al
crear la tabla. Las filas están **físicamente escritas en ese orden** dentro
del SSTable. Ordenar en tiempo de consulta es trabajo que Cassandra
simplemente no hace; lo trasladó al momento de la escritura, donde cuesta
menos.

La tabla de clics usa la misma idea:

```sql
CREATE TABLE clicks_by_code (
    code        text,
    clicked_at  timestamp,
    click_id    uuid,
    referrer    text,
    user_agent  text,
    PRIMARY KEY ((code), clicked_at, click_id)
) WITH CLUSTERING ORDER BY (clicked_at DESC, click_id ASC);
```

El `click_id uuid` al final de la clave no es decorativo: si dos clics
caen en el mismo milisegundo, sin él la segunda fila sobrescribiría a la
primera, porque en Cassandra un `INSERT` sobre una clave existente es un
*upsert* silencioso. Es un error fácil de cometer y difícil de notar: no
falla nada, simplemente pierdes datos.

### Pregunta 4: por qué `COUNT(*)` no es la respuesta

Para el total de clics, el reflejo relacional sería:

```sql
SELECT COUNT(*) FROM clicks_by_code WHERE code = ?;
```

Funciona. Y es una mala idea: recorre la partición entera cada vez. Con un
enlace que acumule cien mil clics, estás leyendo cien mil filas para
devolver un número.

Cassandra tiene un tipo específico para esto:

```sql
CREATE TABLE click_counts (
    code   text PRIMARY KEY,
    total  counter
);
```

```sql
UPDATE click_counts SET total = total + 1 WHERE code = ?;
```

El tipo `counter` resuelve incrementos concurrentes de forma distribuida,
sin el ciclo leer-modificar-escribir que provocaría condiciones de carrera.
Tiene una restricción que lo delata: una tabla con columnas `counter` no
admite ninguna otra columna fuera de la clave primaria. Por eso vive
separada en lugar de ser un campo más de `links_by_code`.

### La escritura doble, en código

Crear un enlace significa escribir en dos tablas:

```python
def create_link(self, code: str, target_url: str, owner: str) -> Link:
    created_at = datetime.now(UTC)
    self._session.execute(
        self._insert_link_by_code, (code, target_url, owner, created_at)
    )
    self._session.execute(
        self._insert_link_by_owner, (owner, created_at, code, target_url)
    )
    return Link(code, target_url, owner, created_at)
```

No hay transacción que las envuelva, porque Cassandra no ofrece
transacciones multi-partición. Lo que sí ofrece es que ambos `INSERT` son
**idempotentes**: si el segundo falla y se reintenta, el resultado es el
mismo. Diseñar para la idempotencia reemplaza aquí al `ROLLBACK`.

Un detalle de rendimiento: todas las sentencias son *prepared statements*.
Se compilan una vez y después viajan por la red como un identificador más
los parámetros. Además, el driver sabe qué nodo es dueño de cada partición
y enruta la petición directamente, sin coordinador intermedio.

---

## La segunda mitad: que el deploy no lo haga un humano

El enunciado pedía despliegue automatizado, y vale la pena tomárselo en
serio, porque "lo subí a la nube" y "tengo un pipeline" son cosas muy
distintas.

La regla que me puse: **el único gesto manual es `git push`**.

```
push a main
   ├─ ruff check        estilo
   ├─ pytest            10 pruebas
   ├─ docker build      la imagen compila
   ├─ POST al deploy hook de Render
   └─ polling a /health hasta que responda
```

Si cualquier paso falla, el deploy no se ejecuta. Un pull request pasa por
lint y pruebas, pero nunca publica.

### El problema del bundle binario

Astra DB no se conecta con una cadena de conexión: exige un **Secure Connect
Bundle**, un `.zip` con los certificados TLS y los metadatos del clúster.

```python
cluster = Cluster(
    cloud={"secure_connect_bundle": ruta_del_bundle},
    auth_provider=PlainTextAuthProvider("token", astra_token),
)
```

En local es un archivo en disco. Pero los secrets de GitHub y de Render
solo guardan texto, no binarios. La solución es codificarlo en base64:

```bash
base64 -w0 secure-connect-shortener-db.zip > bundle.b64
```

y reconstruirlo en memoria al arrancar:

```python
raw = base64.b64decode(settings.astra_scb_b64, validate=True)
handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
handle.write(raw)
handle.close()
return handle.name
```

El bundle nunca entra al repositorio. El `.gitignore` incluye
`secure-connect-*.zip` por si acaso.

### Probar sin credenciales

Un pipeline que necesita secretos de producción para correr sus pruebas es
un pipeline frágil: no funciona en forks y empuja a poner credenciales
reales en CI.

Definí el acceso a datos como un contrato con dos implementaciones: una
contra Cassandra y otra en memoria. Si faltan las credenciales, la
aplicación **arranca igual** con la segunda y lo declara en `/health`:

```json
{"status": "ok", "backend": "memoria", "keyspace": "shortener"}
```

La implementación en memoria respeta el mismo orden de resultados que
imponen las clustering keys, así que las pruebas verifican el contrato real.
En producción `/health` responde `"backend": "astra"`, y el último paso del
pipeline comprueba justamente eso: no basta con que el servicio esté vivo,
tiene que estar hablando con la base de datos correcta.

```yaml
- name: Esperar a que el servicio quede sano
  run: |
    for intento in $(seq 1 30); do
      respuesta=$(curl -fsS "$APP_URL/health" || true)
      if echo "$respuesta" | grep -q '"status":"ok"'; then
        echo "Servicio sano: $respuesta"
        exit 0
      fi
      sleep 10
    done
    exit 1
```

Ese bucle existe por una razón práctica: Render tarda entre uno y tres
minutos en construir la imagen y arrancar el contenedor. Un deploy que
"termina" en cuanto el webhook responde `200` no ha verificado nada.

---

## Lo que me llevo

**La desnormalización deja de ser un pecado.** Tres semestres enseñando
formas normales y aquí la respuesta correcta es duplicar. No es relajar el
rigor: es que el criterio cambió. En un sistema distribuido el costo
dominante es el salto de red, no el espacio en disco.

**La consulta se diseña antes que la tabla.** Es incómodo al principio,
porque obliga a conocer los patrones de acceso antes de escribir una sola
línea. Y es brutalmente honesto: te fuerza a admitir que no sabías cómo se
iba a usar tu aplicación.

**Cassandra no tiene una red de seguridad.** Sin `JOIN`, sin transacciones
multi-partición, sin `ORDER BY` arbitrario, sin secuencias
autoincrementales. Cada una de esas ausencias es un mecanismo de
coordinación que un sistema distribuido no puede pagar barato. Lo que
recibes a cambio es escalado lineal.

**Un despliegue automatizado se nota cuando falla.** El valor del pipeline
no está en el día que todo sale bien: está el día que una prueba se rompe y
el deploy se detiene solo, antes de que la aplicación caída llegue a
producción.

---

Si quieres mirar el código, el esquema CQL está comentado línea por línea
en `app/schema.cql`, que es probablemente el archivo más útil del
repositorio para alguien que esté empezando con Cassandra.

- **Repositorio:** https://github.com/SEBACOR-13/astra-shortener
- **Aplicación:** https://astra-shortener.onrender.com

*Trabajo del curso de Base de Datos II — Universidad Privada de Tacna.*
