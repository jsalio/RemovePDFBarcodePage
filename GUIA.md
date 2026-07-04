# Mini guía de uso

Guía rápida para operar la herramienta. Para detalles técnicos y solución de
problemas, ver el [README](README.md).

## Qué hace

Busca documentos en ProDoctivity, y a los PDFs cuya primera página tenga el
código de barras separador (ej. `EXP-001`) les elimina esa página y sube el
resultado como nueva versión del documento. Nada se borra del servidor: la
versión anterior queda en el historial y el PDF original se guarda también
localmente.

## 1. Preparar (una sola vez)

1. Coloca el archivo `.env` con las credenciales en la carpeta de trabajo
   (junto al `.exe`, o en la raíz del proyecto si usas Python). Si no lo
   tienes, copia `.env.example` y complétalo. Los campos clave:
   - `PD_BARCODE_VALUE` — el valor del código de barras (ej. `EXP-001`)
   - `PD_DOCUMENT_TYPE_IDS` — el tipo de documento a procesar
2. Verifica el espacio en disco: cada documento consume ~40 MB locales
   (original + copia recortada).

## 2. Iniciar

```
RemovePDFBarcodePage.exe          (ejecutable de Windows)
.venv/bin/python main.py          (desde el código)
```

Aparece el menú:

```
=== Reemplazo de PDFs ===
  1) Listado  — generar/actualizar refs.json
  2) Subir    — procesar pendientes y subir nuevas versiones
  0) Salir
```

## 3. Generar el listado (opción 1)

Escribe `1`, confirma el usuario a buscar (Enter acepta el sugerido) y
espera a que recorra todas las páginas. Crea `refs.json` con todos los
documentos en estado `pending`.

Repite esta opción cuando quieras: si se cargaron documentos nuevos en la
organización, los agrega al final **sin perder el avance** de los ya
procesados.

## 4. Procesar y subir (opción 2)

Escribe `2` y responde cuántos documentos procesar:

- Un número (ej. `100`) procesa ese lote y vuelve al menú.
- `*` procesa todo lo pendiente de una vez (revisa antes el espacio en disco).

La cantidad cuenta solo pendientes: los ya trabajados nunca se repiten.
Cada línea del progreso indica el resultado:

- `worked` — tenía el código de barras: página eliminada y versión subida.
- `untouched` — no tenía el código: no se modificó nada.

## 5. Si algo se interrumpe

No pasa nada: el avance se guarda documento a documento en `refs.json`.
Abre de nuevo el programa, entra a la opción 2 y continúa donde quedó.

## Archivos que genera

| Archivo/carpeta | Contenido |
|---|---|
| `refs.json` | El avance del proceso — **no lo borres ni lo edites** |
| `ori_file/` | PDFs originales descargados (respaldo local) |
| `upd_file/` | PDFs recortados que se subieron |

Los PDFs de `ori_file/` y `upd_file/` ya verificados se pueden mover o
borrar para liberar disco; el avance vive en `refs.json`.
