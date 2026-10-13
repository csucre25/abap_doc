"""Verifica que el entorno instalado coincide con las versiones fijadas."""
from importlib import metadata
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _leer_pines(nombre_archivo):
    pines = {}
    for linea in (RAIZ / nombre_archivo).read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if not linea:
            continue
        assert "==" in linea, f"{nombre_archivo}: '{linea}' no tiene versión fija (==)"
        paquete, version = linea.split("==", 1)
        pines[paquete.strip()] = version.strip()
    return pines


def _normalizar(nombre):
    return nombre.lower().replace("_", "-")


@pytest.mark.parametrize("archivo", ["requirements.txt", "requirements.lock"])
def test_versiones_instaladas_coinciden(archivo):
    pines = _leer_pines(archivo)
    assert pines, f"{archivo} está vacío"
    diferencias = []
    for paquete, esperada in pines.items():
        try:
            instalada = metadata.version(paquete)
        except metadata.PackageNotFoundError:
            diferencias.append(f"{paquete}: no instalado (se esperaba {esperada})")
            continue
        if instalada != esperada:
            diferencias.append(f"{paquete}: instalada {instalada}, fijada {esperada}")
    assert not diferencias, "\n".join(diferencias)


def test_dependencias_directas_estan_en_el_lock():
    directas = {_normalizar(p): v for p, v in _leer_pines("requirements.txt").items()}
    lock = {_normalizar(p): v for p, v in _leer_pines("requirements.lock").items()}
    for paquete, version in directas.items():
        assert lock.get(paquete) == version, f"{paquete}=={version} no coincide con requirements.lock"
