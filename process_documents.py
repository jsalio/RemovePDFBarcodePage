"""Fase 2: eliminar la primera página del PDF cuando contiene el código de barras configurado.

Flujo por documento:
    1. Descarga el detalle (incluye el PDF en base64) vía get_document().
    2. Guarda el PDF original en ori_file/<documentId>.pdf.
    3. Renderiza la primera página y decodifica sus códigos de barra.
    4. Si algún código coincide con PD_BARCODE_VALUE del .env, genera una copia
       sin esa página en upd_file/<documentId>.pdf (mismo nombre que el original)
       y la sube como nueva versión del documento (POST /svc/api/documents/),
       conservando la data original.
    5. Actualiza el campo "status" de la entrada en el listado (refs.json) y lo
       guarda tras cada transición, de modo que una corrida interrumpida pueda
       reanudarse donde quedó.

Estados: "pending" (sin procesar) -> "working" (en proceso) ->
"untouched" (descargado, sin código de barras coincidente) | "worked"
(página eliminada y nueva versión subida; newDocumentVersionId queda como
auditoría). Una entrada que quede en "working" tras una interrupción se
reintenta en la siguiente corrida.

Uso:
    python process_documents.py [--listing refs.json] [--limit N]
"""

import argparse
import base64
import io
import json
import os
import sys
import time

import fitz  # PyMuPDF
import requests
import zxingcpp
from PIL import Image
from pypdf import PdfReader, PdfWriter

from search_documents import ApiError, authenticate, get_document, load_config

ORI_DIR = "ori_file"
UPD_DIR = "upd_file"

# Reintentos por documento ante errores de red/API antes de saltarlo.
RETRY_DELAYS = [5, 15, 30]


def load_listing(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_listing(path: str, listing: list) -> None:
    """Guarda el listado de forma atómica para no corromperlo si el proceso muere."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(listing, handle, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def decode_pdf_binary(document: dict) -> bytes:
    """Extrae los bytes del PDF desde el data URI de binaries[0]."""
    binaries = document.get("binaries") or []
    if not binaries:
        raise ApiError(
            f"El documento {document.get('documentId')} no tiene binarios."
        )
    data_uri = binaries[0]
    _, b64 = data_uri.split(",", 1)
    return base64.b64decode(b64)


def first_page_barcodes(pdf_bytes: bytes) -> list:
    """Devuelve los valores de los códigos de barra de la primera página."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        if pdf.page_count == 0:
            return []
        # Zoom 3x (~216 dpi): suficiente para decodificar códigos de barra
        # sin rasterizar a un tamaño excesivo.
        pixmap = pdf[0].get_pixmap(matrix=fitz.Matrix(3, 3))
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return [result.text for result in zxingcpp.read_barcodes(image)]


def remove_first_page(pdf_bytes: bytes) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages[1:]:
        writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def upload_document(
    session: requests.Session,
    config: dict,
    credentials: dict,
    document_type_id: str,
    parent_document_version_id: str,
    pdf_bytes: bytes,
    data: dict = None,
    content_type: str = "application/pdf",
) -> dict:
    """Sube el PDF nuevo como versión del documento vía POST /svc/api/documents/.

    parentDocumentVersionId es el documentVersionId guardado en el listado;
    el PDF va en "documents" como data URI base64 (el API rechaza base64 plano).
    """
    url = config["PD_BASE_URL"] + "/svc/api/documents/"
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    payload = {
        "documentTypeId": document_type_id,
        "parentDocumentVersionId": parent_document_version_id,
        "contentType": content_type,
        "data": data or {},
        "documents": [f"data:{content_type};base64,{b64}"],
    }
    headers = {
        "x-api-key": credentials["apiKey"],
        "x-api-secret": credentials["secret"],
    }
    response = session.post(url, json=payload, headers=headers, timeout=600)
    if not response.ok:
        raise ApiError(
            f"Subida falló ({response.status_code}) en {url}: {response.text[:500]}"
        )
    return response.json()


def process_ref(
    session: requests.Session,
    config: dict,
    credentials: dict,
    ref: dict,
) -> dict:
    """Procesa una entrada del listado; actualiza ref in-place con el resultado."""
    document_id = ref["id"]
    body = get_document(session, config, credentials, document_id)
    document = body["document"]
    pdf_bytes = decode_pdf_binary(document)
    # La data (campos capturados) se necesita para la subida: la nueva versión
    # debe conservar los keywords del documento original.
    data = document.get("data") or {}

    filename = f"{document_id}.pdf"
    with open(os.path.join(ORI_DIR, filename), "wb") as handle:
        handle.write(pdf_bytes)

    barcodes = first_page_barcodes(pdf_bytes)
    matched = config["PD_BARCODE_VALUE"] in barcodes
    if matched:
        updated = remove_first_page(pdf_bytes)
        with open(os.path.join(UPD_DIR, filename), "wb") as handle:
            handle.write(updated)
        upload = upload_document(
            session,
            config,
            credentials,
            document_type_id=document["documentTypeId"],
            parent_document_version_id=ref["documentVersionId"],
            pdf_bytes=updated,
            data=data,
        )
        # Auditoría: versión nueva creada en el servidor.
        ref["newDocumentVersionId"] = upload["documentVersionId"]

    # "worked" implica que la nueva versión ya quedó subida; si la subida
    # falla, la excepción deja el estado en "working" y se reintenta luego.
    ref["status"] = "worked" if matched else "untouched"
    return {
        "documentId": document_id,
        "barcodes": barcodes,
        "matched": matched,
        "data": data,
    }


def process_listing(listing_path: str, limit: int = None) -> int:
    config = load_config()
    if not config.get("PD_BARCODE_VALUE"):
        raise ApiError(
            "Falta PD_BARCODE_VALUE en el .env (valor del código de barras "
            "que identifica la página a eliminar)."
        )
    os.makedirs(ORI_DIR, exist_ok=True)
    os.makedirs(UPD_DIR, exist_ok=True)

    listing = load_listing(listing_path)
    # "working" también cuenta como pendiente: quedó a medias en una corrida
    # interrumpida y debe reintentarse.
    pending = [
        ref for ref in listing if ref.get("status", "pending") in ("pending", "working")
    ]
    print(
        f"{len(pending)} pendiente(s) de {len(listing)} en {listing_path}.",
        file=sys.stderr,
    )
    if limit is not None:
        pending = pending[:limit]

    processed = 0
    failed = 0
    with requests.Session() as session:
        credentials = authenticate(session, config)
        for index, ref in enumerate(pending, start=1):
            # Marcar "working" antes de empezar: si el proceso muere aquí,
            # la siguiente corrida sabe que este documento quedó a medias.
            ref["status"] = "working"
            save_listing(listing_path, listing)

            result = None
            for attempt, delay in enumerate([0] + RETRY_DELAYS):
                if delay:
                    print(
                        f"    reintento {attempt}/{len(RETRY_DELAYS)} de "
                        f"{ref['id']} en {delay}s...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    # Renovar la sesión: el fallo pudo ser por expiración.
                    try:
                        credentials = authenticate(session, config)
                    except (ApiError, requests.RequestException):
                        continue
                try:
                    result = process_ref(session, config, credentials, ref)
                    break
                except (ApiError, requests.RequestException) as error:
                    print(f"    error: {error}", file=sys.stderr)

            if result is None:
                # Se agotaron los reintentos: queda en "working" y se
                # reintentará en la próxima corrida; seguir con el resto.
                failed += 1
                print(
                    f"[{index}/{len(pending)}] {ref['id']}: FALLÓ tras "
                    f"{len(RETRY_DELAYS) + 1} intentos; continúa el siguiente.",
                    file=sys.stderr,
                )
                continue

            save_listing(listing_path, listing)
            processed += 1
            data_info = (
                f"data: {len(result['data'])} campo(s)"
                if result["data"]
                else "data: VACÍA"
            )
            print(
                f"[{index}/{len(pending)}] {result['documentId']}: "
                f"{ref['status']} (códigos: {result['barcodes'] or 'ninguno'}, "
                f"{data_info})",
                file=sys.stderr,
            )
    if failed:
        print(
            f"Advertencia: {failed} documento(s) fallaron y quedaron en "
            "\"working\"; se reintentarán en la próxima corrida.",
            file=sys.stderr,
        )
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Elimina la primera página de los PDFs con el código de barras configurado."
    )
    parser.add_argument(
        "--listing",
        default="refs.json",
        help="Archivo con el listado de la búsqueda (default: refs.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Procesar como máximo N documentos pendientes",
    )
    args = parser.parse_args()

    try:
        processed = process_listing(args.listing, args.limit)
    except ApiError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de conexión: {error}", file=sys.stderr)
        return 1

    print(f"Procesados {processed} documento(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
