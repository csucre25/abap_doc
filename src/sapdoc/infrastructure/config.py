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

from sapdoc.domain.entidades import Modelos, Usuario

LLM_BASE_URL_DEFAULT = "https://integrate.api.nvidia.com/v1"
LLM_MODEL_DEFAULT = "nvidia/nemotron-3-super-120b-a12b"

# Proveedores de IA admitidos. Los valores por defecto de URL, modelo y
# parámetros extra solo existen para "nvidia"; los demás deben indicarlos.
PROVEEDORES = ("nvidia", "openai_compatible", "azure_openai")
# Los modelos Nemotron razonan por defecto; para respuestas JSON se apaga, o el
# "pensar en voz alta" se come el presupuesto de max_tokens.
EXTRA_BODY_NVIDIA = {"chat_template_kwargs": {"enable_thinking": False}}
PLANTILLA_DEFAULT = "plantillas/plantilla_dtss.docx"
USUARIOS_PATH_DEFAULT = "usuarios_permitidos.json"
AUDITORIA_PATH_DEFAULT = "auditoria/eventos.jsonl"
ROLES = ("administrador", "usuario")

_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1"}


class ConfigError(Exception):
    """La configuración es inválida. 'problemas' trae todos los fallos
    encontrados, no solo el primero, para corregirlos de una vez."""

    def __init__(self, problemas):
        self.problemas = list(problemas)
        super().__init__("Configuración inválida:\n" + "\n".join(f"- {p}" for p in self.problemas))


@dataclass(frozen=True)
class Config:
    # repr=False: los secretos no deben aparecer si el objeto termina en un log.
    telegram_token: str = field(repr=False)
    # Usuarios autorizados (archivo JSON aparte). Vacío = nadie puede usar el bot.
    usuarios_path: str
    usuarios: tuple = field(repr=False)
    llm_api_key: str = field(repr=False)
    llm_provider: str
    llm_base_url: str
    llm_api_version: str | None
    llm_extra_body: dict
    modelos: Modelos
    plantilla_path: str
    auditoria_path: str = AUDITORIA_PATH_DEFAULT

    def usuario_telegram(self, telegram_id) -> Usuario | None:
        return next((u for u in self.usuarios if u.telegram_id == telegram_id), None)


def _valor(env, clave, default=None):
    """Una variable vacía o solo con espacios cuenta como no definida."""
    valor = (env.get(clave) or "").strip()
    return valor or default


def _cargar_usuarios(ruta, problemas) -> tuple:
    """Lee el array de usuarios. Si el archivo no existe devuelve () sin error:
    el bot arranca sin autorizar a nadie (falla cerrado) y avisa al arrancar."""
    if not os.path.isfile(ruta):
        return ()
    try:
        # utf-8-sig: el Bloc de notas de Windows puede guardar con BOM.
        with open(ruta, encoding="utf-8-sig") as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        problemas.append(f"{ruta} no es un JSON válido: {e}.")
        return ()
    if not isinstance(datos, list):
        problemas.append(f"{ruta} debe contener un array de usuarios: [ {{...}}, {{...}} ].")
        return ()

    usuarios, vistos = [], set()
    for posicion, dato in enumerate(datos, start=1):
        prefijo = f"{ruta}, usuario {posicion}"
        if not isinstance(dato, dict):
            problemas.append(f"{prefijo}: debe ser un objeto {{\"telegram_id\": ..., \"nombre\": ...}}.")
            continue
        telegram_id, nombre, rol = dato.get("telegram_id"), dato.get("nombre"), dato.get("rol", "usuario")
        errores_previos = len(problemas)
        # type() y no isinstance(): True/False son int en Python y no son IDs válidos.
        if type(telegram_id) is not int:
            problemas.append(f"{prefijo}: 'telegram_id' debe ser un número sin comillas.")
        elif telegram_id in vistos:
            problemas.append(f"{prefijo}: el telegram_id {telegram_id} está repetido.")
        if not isinstance(nombre, str) or not nombre.strip():
            problemas.append(f"{prefijo}: falta 'nombre'.")
        if rol not in ROLES:
            problemas.append(f"{prefijo}: 'rol' debe ser uno de {', '.join(ROLES)} (es {rol!r}).")
        if len(problemas) == errores_previos:
            vistos.add(telegram_id)
            usuarios.append(Usuario(telegram_id=telegram_id, nombre=nombre.strip(), rol=rol))
    return tuple(usuarios)


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

    # Sin archivo o con el array vacío el bot arranca sin autorizar a nadie y le
    # dice a quien escriba su ID, que es como se obtiene para agregarlo.
    usuarios_path = _valor(env, "USUARIOS_PERMITIDOS_PATH", USUARIOS_PATH_DEFAULT)
    usuarios = _cargar_usuarios(usuarios_path, problemas)
    if _valor(env, "TELEGRAM_USUARIOS_PERMITIDOS"):
        # Formato anterior: si se ignorara en silencio, todos quedarían fuera sin saber por qué.
        problemas.append(
            f"TELEGRAM_USUARIOS_PERMITIDOS ya no se usa: pasa los usuarios a {usuarios_path} "
            "(ver usuarios_permitidos.example.json) y borra esa línea del .env."
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

    auditoria_path = _valor(env, "AUDITORIA_PATH", AUDITORIA_PATH_DEFAULT)

    if problemas:
        raise ConfigError(problemas)

    return Config(
        telegram_token=telegram_token,
        usuarios_path=usuarios_path,
        usuarios=usuarios,
        llm_api_key=llm_api_key,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_api_version=llm_api_version,
        llm_extra_body=llm_extra_body,
        modelos=modelos,
        plantilla_path=plantilla_path,
        auditoria_path=auditoria_path,
    )
