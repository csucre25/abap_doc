"""Tests del enchufe de IA (ia.py)."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from openai import AsyncAzureOpenAI, AsyncOpenAI

from config import EXTRA_BODY_NVIDIA, cargar_config
from ia import ClienteIA, RespuestaIA, crear_cliente_ia

PLANTILLA = str(Path(__file__).resolve().parent.parent / "plantilla_dtss.docx")


def config(**extra):
    env = {"TELEGRAM_BOT_TOKEN": "t", "LLM_API_KEY": "k", "PLANTILLA_DTSS_PATH": PLANTILLA,
           "USUARIOS_PERMITIDOS_PATH": str(Path(__file__).resolve().parent / "no_existe_usuarios.json")}
    env.update(extra)
    return cargar_config(env)


class SdkFalso:
    def __init__(self, content="hola", finish_reason="stop"):
        self.recibido = None
        self._eleccion = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.recibido = kwargs
        return SimpleNamespace(choices=[self._eleccion])


def test_completar_arma_la_llamada_y_anade_los_parametros_del_proveedor():
    sdk = SdkFalso()
    cliente = ClienteIA(sdk, extra_body={"x": 1})
    respuesta = asyncio.run(cliente.completar("mi-modelo", "sistema", "usuario", temperature=0.2, max_tokens=50))
    assert respuesta == RespuestaIA(contenido="hola", finish_reason="stop")
    assert sdk.recibido == {
        "model": "mi-modelo",
        "messages": [{"role": "system", "content": "sistema"}, {"role": "user", "content": "usuario"}],
        "temperature": 0.2, "max_tokens": 50, "extra_body": {"x": 1},
    }


def test_sin_parametros_extra_no_se_envia_cuerpo_adicional():
    sdk = SdkFalso()
    asyncio.run(ClienteIA(sdk, extra_body={}).completar("m", "s", "u", temperature=0, max_tokens=1))
    assert sdk.recibido["extra_body"] is None


def test_respuesta_sin_texto_llega_como_cadena_vacia():
    sdk = SdkFalso(content=None, finish_reason="length")
    respuesta = asyncio.run(ClienteIA(sdk).completar("m", "s", "u", temperature=0, max_tokens=1))
    assert respuesta == RespuestaIA(contenido="", finish_reason="length")


def test_nvidia_es_el_proveedor_por_defecto_y_apaga_el_razonamiento():
    cliente = crear_cliente_ia(config())
    assert type(cliente._cliente) is AsyncOpenAI
    assert str(cliente._cliente.base_url).startswith("https://integrate.api.nvidia.com/v1")
    assert cliente._extra_body == EXTRA_BODY_NVIDIA


def test_servicio_compatible_con_openai_no_recibe_parametros_de_nvidia():
    cliente = crear_cliente_ia(config(
        LLM_PROVIDER="openai_compatible", LLM_BASE_URL="http://localhost:8000/v1", LLM_MODEL_DEFAULT="local"))
    assert type(cliente._cliente) is AsyncOpenAI
    assert str(cliente._cliente.base_url).startswith("http://localhost:8000/v1")
    assert cliente._extra_body is None


def test_azure_openai_usa_su_propio_cliente():
    cliente = crear_cliente_ia(config(
        LLM_PROVIDER="azure_openai", LLM_BASE_URL="https://mi-recurso.openai.azure.com",
        LLM_API_VERSION="2024-10-21", LLM_MODEL_DEFAULT="mi-despliegue"))
    assert isinstance(cliente._cliente, AsyncAzureOpenAI)
    assert "mi-recurso.openai.azure.com" in str(cliente._cliente.base_url)
    assert cliente._extra_body is None
