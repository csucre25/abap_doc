# config.py
#
# Única puerta de entrada a la configuración. Todo lo que se pueda cambiar sin
# tocar código se lee aquí desde variables de entorno (.env), se valida al
# arrancar y se entrega al resto de la aplicación como un objeto Config.
# Ningún otro módulo debe llamar a os.getenv.

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from dotenv import load_dotenv

LLM_BASE_URL_DEFAULT = "https://integrate.api.nvidia.com/v1"
LLM_MODEL_DEFAULT = "nvidia/nemotron-3-super-120b-a12b"
PLANTILLA_DEFAULT = "plantilla_dtss.docx"

_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1"}


class ConfigError(Exception):
    """La configuración es inválida. 'problemas' trae todos los fallos
    encontrados, no solo el primero, para corregirlos de una vez."""

    def __init__(self, problemas):
        self.problemas = list(problemas)
        super().__init__("Configuración inválida:\n" + "\n".join(f"- {p}" for p in self.problemas))


@dataclass(frozen=True)
class Modelos:
    """Modelo de IA a usar en cada fase."""
    fase0: str
    fase1: str
    fase1_5: str
    fase2: str
    fase4: str


@dataclass(frozen=True)
class Config:
    # repr=False: los secretos no deben aparecer si el objeto termina en un log.
    telegram_token: str = field(repr=False)
    llm_api_key: str = field(repr=False)
    llm_base_url: str
    modelos: Modelos
    plantilla_path: str


def _valor(env, clave, default=None):
    """Una variable vacía o solo con espacios cuenta como no definida."""
    valor = (env.get(clave) or "").strip()
    return valor or default


def cargar_config(env=None) -> Config:
    """Lee y valida la configuración. Sin argumentos carga el .env y usa el
    entorno del proceso; los tests pasan un dict. Levanta ConfigError."""
    if env is None:
        load_dotenv()
        env = os.environ

    problemas = []

    telegram_token = _valor(env, "TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        problemas.append("Falta TELEGRAM_BOT_TOKEN.")

    # NVIDIA_API_KEY es el nombre histórico; LLM_API_KEY tiene prioridad.
    llm_api_key = _valor(env, "LLM_API_KEY") or _valor(env, "NVIDIA_API_KEY")
    if not llm_api_key:
        problemas.append("Falta LLM_API_KEY (o NVIDIA_API_KEY).")

    llm_base_url = _valor(env, "LLM_BASE_URL", LLM_BASE_URL_DEFAULT)
    url = urlparse(llm_base_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        problemas.append(f"LLM_BASE_URL no es una URL válida: {llm_base_url!r}.")
    elif url.scheme == "http" and url.hostname not in _HOSTS_LOCALES:
        problemas.append(
            f"LLM_BASE_URL debe usar https ({llm_base_url!r}); http solo se admite para un servicio local."
        )

    modelo_default = _valor(env, "LLM_MODEL_DEFAULT", LLM_MODEL_DEFAULT)
    modelos = Modelos(
        fase0=_valor(env, "LLM_MODEL_FASE0", modelo_default),
        fase1=_valor(env, "LLM_MODEL_FASE1", modelo_default),
        fase1_5=_valor(env, "LLM_MODEL_FASE1_5", modelo_default),
        fase2=_valor(env, "LLM_MODEL_FASE2", modelo_default),
        fase4=_valor(env, "LLM_MODEL_FASE4", modelo_default),
    )

    plantilla_path = _valor(env, "PLANTILLA_DTSS_PATH", PLANTILLA_DEFAULT)
    if not os.path.isfile(plantilla_path):
        problemas.append(f"PLANTILLA_DTSS_PATH no apunta a un archivo existente: {plantilla_path!r}.")

    if problemas:
        raise ConfigError(problemas)

    return Config(
        telegram_token=telegram_token,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        modelos=modelos,
        plantilla_path=plantilla_path,
    )
