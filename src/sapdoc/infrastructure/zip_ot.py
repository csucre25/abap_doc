"""Lectura del ZIP que genera el extractor de SAP para una OT."""

import json
import os
import zipfile


class ZipSinMetadataError(Exception):
    """El ZIP de la OT no trae metadata.json en la raíz."""


def leer_zip_ot(zip_path):
    """Devuelve (metadata_json, abap_dict) de un ZIP de OT. abap_dict va
    indexado por nombre de archivo sin carpeta."""
    abap_files_dict = {}
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        if 'metadata.json' not in zip_ref.namelist():
            raise ZipSinMetadataError()
        with zip_ref.open('metadata.json') as meta_file:
            metadata_content = json.load(meta_file)

        for file_name in zip_ref.namelist():
            if file_name.endswith('.abap'):
                # Se lee una sola vez: un segundo read() del mismo stream devuelve b"".
                datos = zip_ref.read(file_name)
                try:
                    content = datos.decode('utf-8')
                except UnicodeDecodeError:
                    content = datos.decode('latin-1')
                abap_files_dict[os.path.basename(file_name)] = content

    return metadata_content, abap_files_dict
