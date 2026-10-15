"""Las capas solo dependen hacia dentro: interfaces -> infraestructura ->
aplicación -> dominio. Un import que rompa esa dirección hace fallar el test."""
import ast
from pathlib import Path

import pytest

PAQUETE = Path(__file__).resolve().parent.parent / "src" / "sapdoc"

# Qué capas puede importar cada capa (además de la biblioteca estándar y terceros).
PERMITIDAS = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "infrastructure": {"domain", "application", "infrastructure"},
    "interfaces": {"domain", "application", "infrastructure", "interfaces"},
}
# Bibliotecas de E/S o de canal que no deben aparecer en las capas internas.
PROHIBIDAS_EN_EL_NUCLEO = {"telegram", "openai", "docx", "docxtpl", "pymupdf", "dotenv", "PIL"}


def _imports(ruta):
    for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Import):
            yield from (a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            yield nodo.module


def _modulos(capa):
    return sorted((PAQUETE / capa).rglob("*.py"))


@pytest.mark.parametrize("capa", PERMITIDAS)
def test_cada_capa_solo_importa_hacia_dentro(capa):
    violaciones = []
    for ruta in _modulos(capa):
        for modulo in _imports(ruta):
            partes = modulo.split(".")
            if partes[0] == "sapdoc" and len(partes) > 1 and partes[1] not in PERMITIDAS[capa]:
                violaciones.append(f"{ruta.relative_to(PAQUETE)} importa {modulo}")
    assert not violaciones, "\n".join(violaciones)


def test_toda_funcion_y_clase_esta_documentada():
    sin_docstring = [
        f"{ruta.relative_to(PAQUETE)}:{nodo.lineno} {nodo.name}"
        for ruta in sorted(PAQUETE.rglob("*.py"))
        for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8")))
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not ast.get_docstring(nodo)
    ]
    assert not sin_docstring, "Sin docstring:\n" + "\n".join(sin_docstring)


@pytest.mark.parametrize("capa", ["domain", "application"])
def test_el_nucleo_no_usa_bibliotecas_de_entrada_salida(capa):
    violaciones = [
        f"{ruta.relative_to(PAQUETE)} importa {modulo}"
        for ruta in _modulos(capa) for modulo in _imports(ruta)
        if modulo.split(".")[0] in PROHIBIDAS_EN_EL_NUCLEO
    ]
    assert not violaciones, "\n".join(violaciones)
