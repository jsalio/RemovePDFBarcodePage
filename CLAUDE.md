# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es este proyecto

Herramienta en Python que elimina la primera página de PDFs almacenados en ProDoctivity cuando esa página contiene un código de barras separador (valor en `PD_BARCODE_VALUE`, ej. `EXP-001`), y sube el PDF recortado como nueva versión del documento. Opera contra el API de ProDoctivity Cloud (`/svc/api`). No hay framework ni tests: son tres módulos planos sobre `requests`.

## Comandos

```bash
.venv/bin/python main.py                    # menú interactivo (punto de entrada normal)
.venv/bin/python search_documents.py --username <user> --refs > refs.json   # fase 1 directa
.venv/bin/python process_documents.py --limit N [--listing refs.json]       # fase 2 directa
.venv/bin/pip install -r requirements.txt   # deps: requests, dotenv, pypdf, PyMuPDF, zxing-cpp, pillow
```

Configuración en `.env` (ver `.env.example`). El `.env` real existe y apunta a producción (`cloud.prodoctivity.com`): **cualquier ejecución de la opción "Subir" crea versiones nuevas de documentos reales**. Para probar sin efectos usa `search_documents.py` (solo lectura) o `--limit 1` consciente de que sube de verdad.

## Arquitectura

Pipeline de dos fases con estado persistente en `refs.json`:

1. **`search_documents.py`** (fase 1, solo lectura): login + búsqueda paginada → lista de referencias `{id, entityType, documentVersionId, status}`.
2. **`process_documents.py`** (fase 2, muta el servidor): por cada referencia pendiente descarga el PDF, guarda el original en `ori_file/<documentId>.pdf`, detecta el código de barras en la primera página (PyMuPDF renderiza a 3x, zxing-cpp decodifica), y si coincide recorta la página (pypdf), guarda en `upd_file/<documentId>.pdf` y sube la nueva versión.
3. **`main.py`**: menú que envuelve ambas fases. Su opción "Listado" hace **merge** sobre `refs.json` (agrega IDs nuevos como `pending`, nunca sobrescribe estados) porque la organización sigue cargando documentos; regenerar el archivo desde cero destruiría el avance.

### Máquina de estados de refs.json

`pending` → `working` (se persiste ANTES de procesar; si el proceso muere, la siguiente corrida lo reintenta) → `untouched` (sin código de barras) | `worked` (recortado Y subido; guarda `newDocumentVersionId` como auditoría). `worked` solo se marca tras subida exitosa. Cada transición se guarda con escritura atómica (`save_listing`: tmp + `os.replace`).

### Autenticación (no estándar)

`POST /svc/api/sessions/login` con `{username, password, organizationId}` + headers `x-api-key`/`x-api-secret` del `.env` **no devuelve un Bearer token**: devuelve `{apiKey, secret}` de sesión, que se pasan como headers `x-api-key`/`x-api-secret` en todas las llamadas siguientes. No hay `Authorization`.

## Reglas del API aprendidas empíricamente (no están en ningún Swagger accesible)

- `rowsPerPage` del search es un **string** y solo acepta `"15"`, `"50"` o `"100"`.
- El único `sortField` de fecha válido es `createdAt` (no `documentDate`, no `$createdAt`).
- Elasticsearch limita `from+size` a 10,000: `search_all_documents` lo evade ordenando por `createdAt` asc y rotando un cursor `dateFrom` (inclusivo) con dedupe por `$documentId`. Si >10,000 documentos comparten la misma fecha, el cursor no avanza y se retorna parcial con advertencia.
- Los campos del resultado de búsqueda llevan prefijo `$` (`$documentId`, `$documentVersionId`); los del detalle (`GET /svc/api/documents/{id}`) no.
- El detalle incluye el binario completo (~20 MB por pasaporte) en `binaries[0]` como data URI; no hay forma de pedirlo sin binario.
- La subida (`POST /svc/api/documents/`) exige el PDF en `documents[0]` como **data URI** (`data:application/pdf;base64,...`); base64 plano da 400. `parentDocumentVersionId` = `documentVersionId` de la lista, y `data` debe repetir los campos del documento original o la nueva versión los pierde.

## Restricciones operativas

- Cada pasaporte pesa ~20 MB; la corrida completa (~1,500 docs) necesita ~50 GB entre `ori_file/` y `upd_file/` y el disco suele no alcanzar — procesar por lotes (`--limit`) o mover las carpetas a otro disco.
- `ori_file/`, `upd_file/`, `refs.json`, `.env` y `.venv` son artefactos de corrida/entorno, no código: no comprometerlos a git si se inicializa un repo.
