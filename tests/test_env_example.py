"""env.example: cada bloque de proveedor, activado solo, da una configuración
válida; y tal como se versiona hay exactamente un bloque activo."""
import io
import re
from pathlib import Path

import pytest
from dotenv import dotenv_values

from sapdoc.infrastructure.config import cargar_config

RAIZ = Path(__file__).resolve().parent.parent
TEXTO = (RAIZ / "env.example").read_text(encoding="utf-8")
BLOQUE = re.compile(r"^# >>> PROVEEDOR: (\w+)\n(.*?)^# <<< FIN \1\n", re.S | re.M)
PROVEEDOR_ESPERADO = {"nvidia": "nvidia", "openai": "openai_compatible", "azure": "azure_openai"}
VARIABLE_ACTIVA = re.compile(r"^[A-Z0-9_]+=", re.M)


def _bloques():
    return {m.group(1): m.group(2) for m in BLOQUE.finditer(TEXTO)}


def _con_solo_activo(nombre):
    """El texto de env.example con ese bloque descomentado y los demás comentados."""
    def ajustar(m):
        cuerpo = m.group(2)
        if m.group(1) == nombre:
            cuerpo = re.sub(r"^# ([A-Z0-9_]+=)", r"\1", cuerpo, flags=re.M)
        else:
            cuerpo = VARIABLE_ACTIVA.sub(lambda v: "# " + v.group(0), cuerpo)
        return f"# >>> PROVEEDOR: {m.group(1)}\n{cuerpo}# <<< FIN {m.group(1)}\n"
    return BLOQUE.sub(ajustar, TEXTO)


def test_hay_un_bloque_por_proveedor():
    assert set(_bloques()) == set(PROVEEDOR_ESPERADO)


def test_tal_como_se_versiona_hay_un_solo_bloque_activo():
    activos = [n for n, cuerpo in _bloques().items() if VARIABLE_ACTIVA.search(cuerpo)]
    assert activos == ["nvidia"]


@pytest.mark.parametrize("nombre", PROVEEDOR_ESPERADO)
def test_cada_bloque_activado_solo_es_una_configuracion_valida(nombre):
    valores = dotenv_values(stream=io.StringIO(_con_solo_activo(nombre)))
    # Los secretos vienen vacíos en el ejemplo; se simulan para validar el resto.
    env = {clave: (valor or "secreto-de-prueba") for clave, valor in valores.items()}
    env["PLANTILLA_DTSS_PATH"] = str(RAIZ / "plantillas" / "plantilla_dtss.docx")
    env["USUARIOS_PERMITIDOS_PATH"] = str(RAIZ / "tests" / "no_existe_usuarios.json")
    config = cargar_config(env)
    assert config.llm_provider == PROVEEDOR_ESPERADO[nombre]


def test_las_variables_del_proveedor_solo_viven_dentro_de_los_bloques():
    fuera = BLOQUE.sub("", TEXTO)
    sueltas = re.findall(r"^#?\s?(LLM_[A-Z0-9_]+)=", fuera, flags=re.M)
    assert sueltas == [], f"Variables de IA fuera de los bloques: {sueltas}"
