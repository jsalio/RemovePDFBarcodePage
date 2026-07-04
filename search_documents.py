"""Fase 1: autenticarse contra el API y listar documentos vía /svc/api/documents/search.

Uso:
    1. Copiar .env.example a .env y completar las credenciales.
    2. pip install -r requirements.txt
    3. python search_documents.py [--query TEXTO] [--page N] [--raw]
"""

import argparse
import json
import sys

import requests
from dotenv import dotenv_values


class ApiError(Exception):
    pass


# Elasticsearch rechaza from+size > 10,000 (index.max_result_window).
ES_MAX_WINDOW = 10000


def load_config() -> dict:
    config = {**dotenv_values(".env")}
    required = [
        "PD_BASE_URL",
        "PD_USERNAME",
        "PD_PASSWORD",
        "PD_ORGANIZATION",
        "PD_API_KEY",
        "PD_API_SECRET",
    ]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ApiError(
            f"Faltan variables en el .env: {', '.join(missing)}. "
            "Copia .env.example a .env y complétalo."
        )
    config.setdefault("PD_LOGIN_PATH", "/svc/api/login")
    config.setdefault("PD_SEARCH_PATH", "/svc/api/documents/search")
    config.setdefault("PD_PAGE_SIZE", "50")
    config["PD_BASE_URL"] = config["PD_BASE_URL"].rstrip("/")
    return config


def authenticate(session: requests.Session, config: dict) -> dict:
    """Hace login y devuelve el apiKey/secret de sesión para las siguientes llamadas."""
    url = config["PD_BASE_URL"] + config["PD_LOGIN_PATH"]
    payload = {
        "username": config["PD_USERNAME"],
        "password": config["PD_PASSWORD"],
        "organizationId": config["PD_ORGANIZATION"],
    }
    headers = {
        "x-api-key": config["PD_API_KEY"],
        "x-api-secret": config["PD_API_SECRET"],
    }
    response = session.post(url, json=payload, headers=headers, timeout=30)
    if not response.ok:
        raise ApiError(
            f"Login falló ({response.status_code}) en {url}: {response.text[:500]}"
        )
    body = response.json()
    if not body.get("success") or not body.get("apiKey") or not body.get("secret"):
        raise ApiError(
            "El login no devolvió las credenciales de sesión esperadas "
            f"(apiKey/secret). Campos recibidos: {list(body.keys())}"
        )
    return {"apiKey": body["apiKey"], "secret": body["secret"]}


def search_documents_page(
    session: requests.Session,
    config: dict,
    credentials: dict,
    username: str,
    document_type_ids: list,
    page_number: int,
    date_from: int = None,
) -> dict:
    url = config["PD_BASE_URL"] + config["PD_SEARCH_PATH"]
    payload = {
        "username": username,
        "pageNumber": page_number,
        "rowsPerPage": config["PD_PAGE_SIZE"],
        # Orden estable por fecha de creación: permite reanudar con dateFrom
        # cuando la búsqueda excede el límite de 10,000 del servidor.
        "sortField": "createdAt",
        "sortDirection": "asc",
    }
    if document_type_ids:
        payload["documentTypeIds"] = document_type_ids
    if date_from is not None:
        payload["dateFrom"] = date_from
    headers = {
        "x-api-key": credentials["apiKey"],
        "x-api-secret": credentials["secret"],
    }
    response = session.post(url, json=payload, headers=headers, timeout=60)
    if not response.ok:
        raise ApiError(
            f"Búsqueda falló ({response.status_code}) en {url}: {response.text[:500]}"
        )
    return response.json()


def search_all_documents(
    session: requests.Session,
    config: dict,
    credentials: dict,
    username: str,
    document_type_ids: list,
) -> list:
    """Recorre todas las páginas y acumula la lista completa de documentos.

    Cuando una búsqueda alcanza el límite de 10,000 resultados del servidor,
    se reinicia con dateFrom = última fecha vista (los resultados van ordenados
    por fecha de creación ascendente) y se deduplica por $documentId, ya que
    dateFrom es inclusivo.
    """
    rows_per_page = int(config["PD_PAGE_SIZE"])
    max_page = ES_MAX_WINDOW // rows_per_page - 1
    documents = {}
    date_from = None
    while True:
        page_number = 0
        chunk_max_date = None
        while True:
            body = search_documents_page(
                session,
                config,
                credentials,
                username,
                document_type_ids,
                page_number,
                date_from,
            )
            batch = extract_document_list(body)
            for doc in batch:
                documents.setdefault(doc.get("$documentId"), doc)
            dates = [d["$documentDate"] for d in batch if d.get("$documentDate")]
            if dates:
                chunk_max_date = max(chunk_max_date or 0, *dates)
            print(
                f"Página {page_number}: {len(batch)} documento(s), "
                f"acumulados {len(documents)}.",
                file=sys.stderr,
            )
            if len(batch) < rows_per_page:
                return list(documents.values())
            if page_number >= max_page:
                break
            page_number += 1
        if chunk_max_date is None or chunk_max_date == date_from:
            print(
                "Advertencia: no se puede avanzar el cursor de fecha (más de "
                f"{ES_MAX_WINDOW} documentos comparten la misma fecha); la "
                "lista puede estar incompleta.",
                file=sys.stderr,
            )
            return list(documents.values())
        date_from = chunk_max_date
        print(
            f"Límite de {ES_MAX_WINDOW} alcanzado; continuando desde "
            f"dateFrom={date_from}.",
            file=sys.stderr,
        )


def extract_document_list(body) -> list:
    """La respuesta puede ser una lista directa o venir envuelta; se normaliza a lista."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("documents", "items", "results", "data"):
            if isinstance(body.get(key), list):
                return body[key]
    raise ApiError(
        "No se pudo identificar la lista de documentos en la respuesta. "
        "Ejecuta con --raw para ver la respuesta completa."
    )


def get_document(
    session: requests.Session,
    config: dict,
    credentials: dict,
    document_id: str,
) -> dict:
    """Obtiene el detalle de un documento vía GET /svc/api/documents/{documentId}."""
    url = f"{config['PD_BASE_URL']}/svc/api/documents/{document_id}"
    headers = {
        "x-api-key": credentials["apiKey"],
        "x-api-secret": credentials["secret"],
    }
    # El detalle incluye el binario completo (~25 MB): en conexiones lentas
    # la lectura puede tardar varios minutos.
    response = session.get(url, headers=headers, timeout=600)
    if not response.ok:
        raise ApiError(
            f"Obtener documento falló ({response.status_code}) en {url}: "
            f"{response.text[:500]}"
        )
    return response.json()


def extract_document_refs(documents: list) -> list:
    """Convierte el listado de la búsqueda en pares {id, entityType}.

    Es el formato de referencia que consumen las siguientes llamadas al API.
    """
    return [
        {
            "id": doc["$documentId"],
            "entityType": doc["$entityType"],
            "documentVersionId": doc["$documentVersionId"],
            # Ciclo de vida: pending -> working -> untouched | worked
            "status": "pending",
        }
        for doc in documents
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Buscar documentos en el API.")
    parser.add_argument(
        "--username",
        default="",
        help="Usuario para el campo username del body (default: PD_USERNAME del .env)",
    )
    parser.add_argument(
        "--document-type-ids",
        default="",
        help="IDs de tipo de documento separados por coma "
        "(si se omite, se usa PD_DOCUMENT_TYPE_IDS del .env)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Imprime la respuesta completa de la primera página sin procesar",
    )
    parser.add_argument(
        "--refs",
        action="store_true",
        help="Imprime solo pares {id, entityType} en lugar del documento completo",
    )
    parser.add_argument(
        "--first",
        action="store_true",
        help="Toma solo el primer documento de la lista y obtiene su detalle "
        "vía GET /svc/api/documents/{documentId}",
    )
    args = parser.parse_args()

    try:
        config = load_config()
        ids_source = args.document_type_ids or config.get("PD_DOCUMENT_TYPE_IDS") or ""
        document_type_ids = [i.strip() for i in ids_source.split(",") if i.strip()]
        username = args.username or config["PD_USERNAME"]
        with requests.Session() as session:
            credentials = authenticate(session, config)
            print("Autenticación OK.", file=sys.stderr)
            if args.raw:
                body = search_documents_page(
                    session, config, credentials, username, document_type_ids, 0
                )
                print(json.dumps(body, indent=2, ensure_ascii=False))
                return 0
            if args.first:
                body = search_documents_page(
                    session, config, credentials, username, document_type_ids, 0
                )
                refs = extract_document_refs(extract_document_list(body))
                if not refs:
                    raise ApiError("La búsqueda no devolvió documentos.")
                first = refs[0]
                print(
                    f"Primer documento: {first['id']} "
                    f"(entityType={first['entityType']}).",
                    file=sys.stderr,
                )
                detail = get_document(session, config, credentials, first["id"])
                print(json.dumps(detail, indent=2, ensure_ascii=False))
                return 0
            documents = search_all_documents(
                session, config, credentials, username, document_type_ids
            )
    except ApiError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"Error de conexión: {error}", file=sys.stderr)
        return 1

    print(f"Total: {len(documents)} documento(s).", file=sys.stderr)
    output = extract_document_refs(documents) if args.refs else documents
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
