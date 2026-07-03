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

- **1) Listado** — busca los documentos del usuario indicado y genera/actualiza `refs.json`. Si el archivo ya existe hace *merge*: agrega solo los IDs nuevos como `pending` y conserva el avance existente.
- **2) Subir** — procesa los pendientes. Pregunta cuántos documentos procesar, o `*` para todo el archivo.

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

## Consideraciones

- **Espacio en disco**: cada PDF ronda los 20 MB y se guarda dos veces (original y recortado). Estima ~40 MB por documento y usa `--limit` para procesar por lotes si el disco no alcanza.
- **La opción "Subir" muta el servidor**: cada documento con código de barras genera una versión nueva real. El original queda como versión anterior en ProDoctivity y como respaldo local en `ori_file/`.
- **No regenerar `refs.json` a mano**: usa la opción "Listado" del menú, que hace merge sin perder estados.
- El servidor limita las búsquedas a 10,000 resultados; el script lo supera automáticamente con un cursor por fecha de creación.
