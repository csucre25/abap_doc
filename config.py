# config.py
#
# Única puerta de entrada a la configuración. Todo lo que se pueda cambiar sin
# tocar código se lee aquí desde variables de entorno (.env), se valida al
# arrancar y se entrega al resto de la aplicación como un objeto Config.
# Ningún otro módulo debe llamar a os.getenv.

import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from dotenv import load_dotenv

LLM_BASE_URL_DEFAULT = "https://integrate.api.nvidia.com/v1"
LLM_MODEL_DEFAULT = "nvidia/nemotron-3-super-120b-a12b"

# Proveedores de IA admitidos. Los valores por defecto de URL, modelo y
# parámetros extra solo existen para "nvidia"; los demás deben indicarlos.
PROVEEDORES = ("nvidia", "openai_compatible", "azure_openai")
# Los modelos Nemotron razonan por defecto; para respuestas JSON se apaga, o el
# "pensar en voz alta" se come el presupuesto de max_tokens.
EXTRA_BODY_NVIDIA = {"chat_template_kwargs": {"enable_thinking": False}}
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
    # IDs numéricos de Telegram autorizados. Vacío = nadie puede usar el bot.
    telegram_usuarios_permitidos: frozenset
    llm_api_key: str = field(repr=False)
    llm_provider: str
    llm_base_url: str
    llm_api_version: str | None
    llm_extra_body: dict
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

    # Se admite vacío a propósito: el bot arranca sin autorizar a nadie y le
    # dice a quien escriba su ID, que es como se obtiene para agregarlo aquí.
    telegram_usuarios_permitidos = set()
    for item in (_valor(env, "TELEGRAM_USUARIOS_PERMITIDOS") or "").split(","):
        item = item.strip()
        if not item:
            continue
        if item.lstrip("-").isdigit():
            telegram_usuarios_permitidos.add(int(item))
        else:
            problemas.append(
                f"TELEGRAM_USUARIOS_PERMITIDOS solo admite IDs numéricos separados por comas ({item!r} no lo es)."
            )

    # NVIDIA_API_KEY es el nombre histórico; LLM_API_KEY tiene prioridad.
    llm_api_key = _valor(env, "LLM_API_KEY") or _valor(env, "NVIDIA_API_KEY")
    if not llm_api_key:
        problemas.append("Falta LLM_API_KEY (o NVIDIA_API_KEY).")

    llm_provider = _valor(env, "LLM_PROVIDER", "nvidia").lower()
    if llm_provider not in PROVEEDORES:
        problemas.append(f"LLM_PROVIDER debe ser uno de {', '.join(PROVEEDORES)} (es {llm_provider!r}).")
    es_nvidia = llm_provider == "nvidia"

    llm_base_url = _valor(env, "LLM_BASE_URL", LLM_BASE_URL_DEFAULT if es_nvidia else None)
    if not llm_base_url:
        problemas.append(f"Falta LLM_BASE_URL (obligatoria con LLM_PROVIDER={llm_provider}).")
    else:
        url = urlparse(llm_base_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            problemas.append(f"LLM_BASE_URL no es una URL válida: {llm_base_url!r}.")
        elif url.scheme == "http" and url.hostname not in _HOSTS_LOCALES:
            problemas.append(
                f"LLM_BASE_URL debe usar https ({llm_base_url!r}); http solo se admite para un servicio local."
            )

    llm_api_version = _valor(env, "LLM_API_VERSION")
    if llm_provider == "azure_openai" and not llm_api_version:
        problemas.append("Falta LLM_API_VERSION (obligatoria con LLM_PROVIDER=azure_openai).")

    # Parámetros propios del proveedor que se añaden a cada llamada. "{}" los desactiva.
    llm_extra_body = EXTRA_BODY_NVIDIA if es_nvidia else {}
    extra_crudo = _valor(env, "LLM_EXTRA_BODY")
    if extra_crudo:
        try:
            llm_extra_body = json.loads(extra_crudo)
        except json.JSONDecodeError:
            llm_extra_body = None
        if not isinstance(llm_extra_body, dict):
            problemas.append("LLM_EXTRA_BODY debe ser un objeto JSON, por ejemplo {} o {\"clave\": \"valor\"}.")

    modelo_default = _valor(env, "LLM_MODEL_DEFAULT", LLM_MODEL_DEFAULT if es_nvidia else None)
    modelos = Modelos(
        fase0=_valor(env, "LLM_MODEL_FASE0", modelo_default),
        fase1=_valor(env, "LLM_MODEL_FASE1", modelo_default),
        fase1_5=_valor(env, "LLM_MODEL_FASE1_5", modelo_default),
        fase2=_valor(env, "LLM_MODEL_FASE2", modelo_default),
        fase4=_valor(env, "LLM_MODEL_FASE4", modelo_default),
    )
    if not all(vars(modelos).values()):
        problemas.append(
            f"Falta LLM_MODEL_DEFAULT (con LLM_PROVIDER={llm_provider} no hay modelo por defecto; "
            "en Azure es el nombre del despliegue)."
        )

    plantilla_path = _valor(env, "PLANTILLA_DTSS_PATH", PLANTILLA_DEFAULT)
    if not os.path.isfile(plantilla_path):
        problemas.append(f"PLANTILLA_DTSS_PATH no apunta a un archivo existente: {plantilla_path!r}.")

    if problemas:
        raise ConfigError(problemas)

    return Config(
        telegram_token=telegram_token,
        telegram_usuarios_permitidos=frozenset(telegram_usuarios_permitidos),
        llm_api_key=llm_api_key,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_api_version=llm_api_version,
        llm_extra_body=llm_extra_body,
        modelos=modelos,
        plantilla_path=plantilla_path,
    )
