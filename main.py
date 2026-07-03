"""Punto de entrada principal: menú interactivo del proceso completo.

Opciones:
    1) Listado — busca los documentos y genera/actualiza refs.json. Si el
       archivo ya existe hace merge: agrega solo los IDs nuevos como "pending"
       y conserva los estados de los ya existentes.
    2) Subir — procesa los pendientes (descarga, recorte de página con código
       de barras y subida de la nueva versión). Pregunta la cantidad a
       procesar, o "*" para todo el archivo.

Uso:
    python main.py
"""

import json
import os
import sys

import requests

from process_documents import process_listing, save_listing
from search_documents import (
    ApiError,
    authenticate,
    extract_document_refs,
    load_config,
    search_all_documents,
)

LISTING_FILE = "refs.json"


def merge_listing(path: str, refs: list) -> None:
    """Agrega a `path` solo las referencias nuevas, conservando los estados."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            listing = json.load(handle)
        known = {ref["id"] for ref in listing}
        new = [ref for ref in refs if ref["id"] not in known]
        listing.extend(new)
        save_listing(path, listing)
        print(
            f"{len(new)} referencia(s) nueva(s) agregadas; "
            f"{len(listing)} en total en {path}."
        )
    else:
        save_listing(path, refs)
        print(f"Listado creado con {len(refs)} referencia(s) en {path}.")


def action_listing(config: dict) -> None:
    default_user = config.get("PD_SEARCH_USERNAME") or config["PD_USERNAME"]
    username = input(f"Usuario a buscar [{default_user}]: ").strip() or default_user
    ids_source = config.get("PD_DOCUMENT_TYPE_IDS") or ""
    document_type_ids = [i.strip() for i in ids_source.split(",") if i.strip()]

    with requests.Session() as session:
        credentials = authenticate(session, config)
        documents = search_all_documents(
            session, config, credentials, username, document_type_ids
        )
    merge_listing(LISTING_FILE, extract_document_refs(documents))


def action_upload() -> None:
    answer = input("¿Cuántos documentos procesar? (* = todo el archivo): ").strip()
    if answer == "*":
        limit = None
    else:
        try:
            limit = int(answer)
        except ValueError:
            print("Cantidad inválida: escribe un número o *.")
            return
        if limit <= 0:
            print("La cantidad debe ser mayor que cero.")
            return
    processed = process_listing(LISTING_FILE, limit)
    print(f"Procesados {processed} documento(s).")


def main() -> int:
    while True:
        print(
            "\n=== Reemplazo de PDFs ===\n"
            "  1) Listado  — generar/actualizar refs.json\n"
            "  2) Subir    — procesar pendientes y subir nuevas versiones\n"
            "  0) Salir"
        )
        choice = input("Opción: ").strip()
        try:
            if choice == "1":
                action_listing(load_config())
            elif choice == "2":
                action_upload()
            elif choice == "0":
                return 0
            else:
                print("Opción inválida.")
        except ApiError as error:
            print(f"Error: {error}", file=sys.stderr)
        except requests.RequestException as error:
            print(f"Error de conexión: {error}", file=sys.stderr)
        except FileNotFoundError as error:
            print(f"Archivo no encontrado: {error}", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nInterrumpido; el avance quedó guardado en el listado.")
        sys.exit(130)
