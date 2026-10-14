"""Tests de la configuración central (config.py)."""
import subprocess
import sys
from pathlib import Path

import pytest

from config import (
    LLM_BASE_URL_DEFAULT, LLM_MODEL_DEFAULT, ConfigError, cargar_config,
)

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLA = str(RAIZ / "plantilla_dtss.docx")


def env(**extra):
    base = {"TELEGRAM_BOT_TOKEN": "token-tg", "NVIDIA_API_KEY": "clave-nvidia", "PLANTILLA_DTSS_PATH": PLANTILLA}
    base.update(extra)
    return base


def test_valores_por_defecto():
    config = cargar_config(env())
    assert config.telegram_token == "token-tg"
    assert config.llm_api_key == "clave-nvidia"
    assert config.llm_base_url == LLM_BASE_URL_DEFAULT
    assert config.plantilla_path == PLANTILLA
    m = config.modelos
    assert {m.fase0, m.fase1, m.fase1_5, m.fase2, m.fase4} == {LLM_MODEL_DEFAULT}


def test_modelo_por_fase_y_default_propio():
    m = cargar_config(env(LLM_MODEL_DEFAULT="base", LLM_MODEL_FASE2="analista", LLM_MODEL_FASE4="  ")).modelos
    assert (m.fase0, m.fase1, m.fase1_5, m.fase2) == ("base", "base", "base", "analista")
    assert m.fase4 == "base"  # una variable en blanco cuenta como no definida


def test_llm_api_key_tiene_prioridad_sobre_el_nombre_historico():
    assert cargar_config(env(LLM_API_KEY="clave-nueva")).llm_api_key == "clave-nueva"


def test_proveedor_configurable_por_url():
    assert cargar_config(env(LLM_BASE_URL="https://ia.interna.example/v1")).llm_base_url == "https://ia.interna.example/v1"
    assert cargar_config(env(LLM_BASE_URL="http://localhost:8000/v1")).llm_base_url == "http://localhost:8000/v1"


@pytest.mark.parametrize("url", ["http://ia.externa.example/v1", "ftp://x/v1", "no-es-url"])
def test_url_insegura_o_invalida_se_rechaza(url):
    with pytest.raises(ConfigError) as error:
        cargar_config(env(LLM_BASE_URL=url))
    assert len(error.value.problemas) == 1 and "LLM_BASE_URL" in error.value.problemas[0]


def test_reporta_todos_los_problemas_juntos():
    with pytest.raises(ConfigError) as error:
        cargar_config({"PLANTILLA_DTSS_PATH": "no_existe.docx"})
    texto = str(error.value)
    assert len(error.value.problemas) == 3
    assert "TELEGRAM_BOT_TOKEN" in texto and "LLM_API_KEY" in texto and "no_existe.docx" in texto


def test_los_secretos_no_aparecen_al_imprimir_la_config():
    texto = repr(cargar_config(env()))
    assert "token-tg" not in texto and "clave-nvidia" not in texto


def test_solo_config_lee_variables_de_entorno():
    ofensores = [
        p.name for p in RAIZ.glob("*.py")
        if p.name != "config.py" and any(s in p.read_text(encoding="utf-8") for s in ("os.getenv", "os.environ", "load_dotenv"))
    ]
    assert ofensores == []


def test_el_bot_importa_sin_configuracion():
    # Importar bot.py no debe exigir credenciales ni crear clientes: eso ocurre en main().
    proceso = subprocess.run(
        [sys.executable, "-c", "import bot"], cwd=RAIZ, capture_output=True, text=True,
        env={"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": ""},
    )
    assert proceso.returncode == 0, proceso.stderr
