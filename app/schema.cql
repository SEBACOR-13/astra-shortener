-- =====================================================================
--  Esquema CQL del acortador de URLs
--  Regla de oro en Cassandra: NO se modela por entidades, se modela
--  por CONSULTAS. Cada tabla existe para responder una pregunta concreta
--  en una sola particion, sin JOINs y sin indices secundarios.
-- =====================================================================

-- Pregunta 1: "dado un codigo corto, a que URL redirijo?"
-- Acceso por clave primaria simple -> una particion, lectura O(1).
CREATE TABLE IF NOT EXISTS links_by_code (
    code        text PRIMARY KEY,
    target_url  text,
    owner       text,
    created_at  timestamp
);

-- Pregunta 2: "que enlaces creo este usuario, del mas nuevo al mas viejo?"
-- Misma informacion que la tabla anterior, DUPLICADA a proposito.
-- partition key = owner  -> todos sus enlaces viven juntos en un nodo
-- clustering key = created_at DESC -> ya vienen ordenados desde disco
CREATE TABLE IF NOT EXISTS links_by_owner (
    owner       text,
    created_at  timestamp,
    code        text,
    target_url  text,
    PRIMARY KEY ((owner), created_at, code)
) WITH CLUSTERING ORDER BY (created_at DESC, code ASC);

-- Pregunta 3: "cuales fueron los ultimos clics de este enlace?"
-- Serie temporal acotada por particion: la particion es el codigo,
-- nunca crece sin control porque consultamos siempre con LIMIT.
CREATE TABLE IF NOT EXISTS clicks_by_code (
    code        text,
    clicked_at  timestamp,
    click_id    uuid,
    referrer    text,
    user_agent  text,
    PRIMARY KEY ((code), clicked_at, click_id)
) WITH CLUSTERING ORDER BY (clicked_at DESC, click_id ASC);

-- Pregunta 4: "cuantos clics lleva en total este enlace?"
-- Contar filas en Cassandra es caro (full scan de particion). Para esto
-- existe el tipo counter, que resuelve incrementos concurrentes sin
-- leer-modificar-escribir. Una tabla counter solo admite columnas
-- counter fuera de la clave primaria: por eso vive separada.
CREATE TABLE IF NOT EXISTS click_counts (
    code   text PRIMARY KEY,
    total  counter
);
