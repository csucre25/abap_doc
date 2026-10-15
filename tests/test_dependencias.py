"""Verifica que el entorno instalado coincide con las versiones fijadas."""
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _pines(lineas, origen):
    pines = {}
    for linea in lineas:
        linea = linea.split("#", 1)[0].strip()
        if not linea:
            continue
        assert "==" in linea, f"{origen}: '{linea}' no tiene versión fija (==)"
        paquete, version = linea.split("==", 1)
        pines[_normalizar(paquete)] = version.strip()
    return pines


def _normalizar(nombre):
    return nombre.strip().lower().replace("_", "-")


def _pyproject():
    with open(RAIZ / "pyproject.toml", "rb") as f:
        proyecto = tomllib.load(f)["project"]
    return {
        "dependencias": _pines(proyecto["dependencies"], "pyproject.toml"),
        "dev": _pines(proyecto["optional-dependencies"]["dev"], "pyproject.toml [dev]"),
    }


def _lock():
    return _pines((RAIZ / "requirements.lock").read_text(encoding="utf-8").splitlines(), "requirements.lock")


@pytest.mark.parametrize("origen", ["dependencias", "dev", "lock"])
def test_versiones_instaladas_coinciden(origen):
    pines = _lock() if origen == "lock" else _pyproject()[origen]
    assert pines, f"{origen} está vacío"
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
    lock = _lock()
    for paquete, version in _pyproject()["dependencias"].items():
        assert lock.get(paquete) == version, f"{paquete}=={version} no coincide con requirements.lock"
