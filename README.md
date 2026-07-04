# replace-pd-pdf-fp

Herramienta para eliminar la primera página de PDFs almacenados en ProDoctivity cuando contiene un código de barras separador (por ejemplo `EXP-001`), subiendo el PDF recortado como nueva versión del documento y conservando sus datos capturados.

## Requisitos

- Python 3.9+
- Credenciales del API de ProDoctivity (usuario, contraseña, organización, apiKey y apiSecret)

## Instalación

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # completar con los valores reales
```

### Variables del `.env`

| Variable | Descripción |
|---|---|
| `PD_BASE_URL` | URL base del servidor, sin slash final |
| `PD_USERNAME` / `PD_PASSWORD` / `PD_ORGANIZATION` | Credenciales de login |
| `PD_API_KEY` / `PD_API_SECRET` | Llaves del API (headers del login) |
| `PD_LOGIN_PATH` | Ruta del login (default `/svc/api/login`; en cloud es `/svc/api/sessions/login`) |
| `PD_SEARCH_PATH` | Ruta de búsqueda (default `/svc/api/documents/search`) |
| `PD_PAGE_SIZE` | Tamaño de página: `15`, `50` o `100` (únicos valores que acepta el API) |
| `PD_DOCUMENT_TYPE_IDS` | IDs de tipo de documento a filtrar, separados por coma |
| `PD_BARCODE_VALUE` | Valor del código de barras que identifica la página a eliminar |

## Uso

### Menú interactivo (recomendado)

```bash
.venv/bin/python main.py
```

```
=== Reemplazo de PDFs ===
  1) Listado  — generar/actualizar refs.json
  2) Subir    — procesar pendientes y subir nuevas versiones
  0) Salir
Opción:
```

**Opción 1 — Listado.** Pide el usuario cuyos documentos se van a buscar
(Enter acepta el valor entre corchetes, tomado del `.env`) y recorre la
búsqueda completa filtrando por `PD_DOCUMENT_TYPE_IDS`:

```
Opción: 1
Usuario a buscar [usuario@dominio.com]:
205 referencia(s) nueva(s) agregadas; 1506 en total en refs.json.
```

Si `refs.json` no existe, lo crea con todas las referencias en `pending`.
Si ya existe hace *merge*: agrega solo los IDs nuevos y conserva el avance
existente. Por eso es seguro (y recomendable) repetir esta opción cuando la
organización sigue cargando documentos.

**Opción 2 — Subir.** Pregunta cuántos documentos pendientes procesar; un
número procesa ese lote y `*` procesa todo el archivo:

```
Opción: 2
¿Cuántos documentos procesar? (* = todo el archivo): 10
1504 pendiente(s) de 1506 en refs.json.
[1/10] 6a47f27d5748d7a09937f3b3: worked (códigos: ['EXP-001', 'EXP-001'], data: 13 campo(s))
[2/10] 6a47f280c7b18f4881b55d29: untouched (códigos: ninguno, data: 12 campo(s))
...
Procesados 10 documento(s).
```

Cada línea muestra el resultado del documento: `worked` (página eliminada y
nueva versión subida) o `untouched` (sin código de barras coincidente), los
códigos detectados en la primera página y cuántos campos de data conserva.

**Interrupción y reanudación.** Se puede cortar en cualquier momento
(Ctrl+C); el avance queda guardado en `refs.json`. Al volver a entrar a la
opción 2, continúa con los `pending` y reintenta los que quedaron en
`working`. Los errores de API o de conexión se muestran y regresan al menú.

### Scripts individuales

```bash
# Fase 1 — solo lectura: genera el listado de referencias
.venv/bin/python search_documents.py --username usuario@dominio.com --refs > refs.json

# Fase 2 — procesa y SUBE nuevas versiones al servidor
.venv/bin/python process_documents.py [--limit N] [--listing refs.json]
```

Otras opciones de `search_documents.py`: `--document-type-ids id1,id2`, `--raw` (primera página cruda), `--first` (detalle del primer documento).

## Flujo del proceso

```
login ──► búsqueda paginada ──► refs.json (status: pending)
                                    │
              por cada pendiente:   ▼
        GET /svc/api/documents/{id} ──► ori_file/<documentId>.pdf
                                    │
              ¿código de barras en la 1ra página == PD_BARCODE_VALUE?
                    │ no                     │ sí
                    ▼                        ▼
                untouched          eliminar 1ra página
                                             │
                              upd_file/<documentId>.pdf
                                             │
                              POST /svc/api/documents/  (nueva versión,
                              misma data, parent = documentVersionId)
                                             │
                                             ▼
                                  worked + newDocumentVersionId
```

### Estados en `refs.json`

| Estado | Significado |
|---|---|
| `pending` | Sin procesar |
| `working` | En proceso (si el script muere aquí, se reintenta en la siguiente corrida) |
| `untouched` | Descargado; la primera página no tenía el código de barras |
| `worked` | Página eliminada y nueva versión subida (`newDocumentVersionId` queda como auditoría) |

El listado se guarda con escritura atómica tras cada transición: el proceso puede interrumpirse en cualquier momento y reanudarse relanzando el mismo comando.

## Ejecutable de Windows

Hay dos formas de obtener `RemovePDFBarcodePage.exe`:

1. **Compilarlo en una máquina Windows** (requiere Python 3.9+):

   ```bat
   build_windows.bat
   ```

   El ejecutable queda en `dist\RemovePDFBarcodePage.exe`.

2. **GitHub Actions**: el workflow `build-windows.yml` compila el ejecutable
   en un runner de Windows al lanzarlo manualmente (pestaña Actions → Build
   Windows executable → Run workflow) o al pushear un tag `v*`. El `.exe`
   queda como artefacto descargable del run. Requiere que la cuenta tenga
   GitHub Actions habilitado (facturación al día).

Para usarlo: copia el `.env` junto al `.exe` (o ejecuta el `.exe` desde una
carpeta que lo contenga). `refs.json`, `ori_file/` y `upd_file/` se crean en
esa misma carpeta de trabajo.

## Preguntas frecuentes y troubleshooting

**Si pido 100 y ya hay 20 trabajados, ¿qué hace?**
La cantidad aplica solo a los pendientes. Los `worked` y `untouched` se
excluyen antes de contar: con 1,506 referencias y 20 trabajadas, responder
`100` procesa 100 documentos nuevos (no 80) y deja 1,386 pendientes. La
cantidad siempre significa "cuántos más avanzar ahora"; se puede correr por
lotes indefinidamente sin duplicar trabajo.

**El proceso se interrumpió (Ctrl+C, corte de red, error).**
No hay que hacer nada especial: el avance se guarda tras cada documento.
Relanza el menú y entra de nuevo a "Subir"; continúa con los `pending` y
reintenta los que quedaron en `working`.

**¿Puedo repetir la opción "Listado" sin perder el avance?**
Sí. Hace merge: agrega solo los IDs nuevos como `pending` y no toca los
estados existentes. Lo que no debes hacer es regenerar `refs.json`
redirigiendo la salida de `search_documents.py --refs` sobre el mismo
archivo: eso sí reinicia todos los estados.

**¿Cómo reproceso un documento puntual?**
Edita su entrada en `refs.json` y cambia `status` a `"pending"`. Ojo: si ya
se había subido, la versión actual del servidor ya no tiene la página del
código de barras, por lo que terminará en `untouched` (no se recorta dos
veces).

**Login falla con 403 Forbidden.**
Credenciales o formato incorrecto. Verifica que el body del login use
`organizationId` (no `organization`) y que `PD_LOGIN_PATH` sea la ruta
correcta de tu instalación (en cloud: `/svc/api/sessions/login`).

**ValidationError con `rowsPerPage`.**
El API solo acepta `"15"`, `"50"` o `"100"` (como string). Ajusta
`PD_PAGE_SIZE` a uno de esos valores.

**Error 500 "Result window is too large".**
Es el límite de 10,000 resultados de Elasticsearch. El script lo maneja
automáticamente con un cursor por fecha (`sortField: createdAt` +
`dateFrom`); si lo ves, probablemente estás llamando al API por fuera del
script. Solo queda sin salida si más de 10,000 documentos comparten la misma
fecha de creación (el script lo advierte y devuelve lo acumulado).

**Subida falla con "document data must be in Data URI format".**
El PDF en `documents[0]` debe ir como `data:application/pdf;base64,...`;
base64 plano se rechaza. El script ya lo envía así.

**El log muestra `data: VACÍA`.**
El documento no tiene campos capturados. La subida funciona igual, pero
revisa que sea esperado: la `data` que se envía es la que conservará la
nueva versión.

**Error de conexión: "Read timed out".**
Un tropiezo de red o una conexión lenta con los PDFs grandes (~25 MB por
transferencia). El script reintenta cada documento hasta 4 veces (esperas de
5/15/30 s, renovando la sesión por si expiró) y, si aun así falla, lo deja
en `working` y **continúa con el siguiente** — un documento problemático no
aborta el lote. Los fallidos se reintentan automáticamente en la próxima
corrida. Los timeouts de descarga y subida son de 10 minutos por request.

**Se llenó el disco.**
Cada documento guarda original y copia (~40 MB por pasaporte). Procesa por
lotes y mueve o borra el contenido de `ori_file/`/`upd_file/` ya verificado;
el estado vive en `refs.json`, no en las carpetas, así que borrar PDFs
locales no afecta la reanudación.

## Consideraciones

- **Espacio en disco**: cada PDF ronda los 20 MB y se guarda dos veces (original y recortado). Estima ~40 MB por documento y usa `--limit` para procesar por lotes si el disco no alcanza.
- **La opción "Subir" muta el servidor**: cada documento con código de barras genera una versión nueva real. El original queda como versión anterior en ProDoctivity y como respaldo local en `ori_file/`.
- **No regenerar `refs.json` a mano**: usa la opción "Listado" del menú, que hace merge sin perder estados.
- El servidor limita las búsquedas a 10,000 resultados; el script lo supera automáticamente con un cursor por fecha de creación.
