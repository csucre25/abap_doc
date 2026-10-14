# ia.py
#
# Enchufe del proveedor de IA. El resto de la aplicación solo conoce
# ClienteIA.completar(); qué servicio hay detrás (NVIDIA, Azure OpenAI u otro
# compatible con la API de OpenAI) se decide en config.py con LLM_PROVIDER.

from dataclasses import dataclass

from openai import AsyncAzureOpenAI, AsyncOpenAI

from config import Config


@dataclass(frozen=True)
class RespuestaIA:
    contenido: str        # nunca None: una respuesta sin texto llega como ""
    finish_reason: str | None = None


class ClienteIA:
    """Envuelve un cliente tipo OpenAI y le añade los parámetros propios del
    proveedor ('extra_body'), para que el núcleo no dependa de ninguno."""

    def __init__(self, cliente, extra_body: dict | None = None):
        self._cliente = cliente
        self._extra_body = extra_body or None

    async def completar(self, modelo: str, system: str, user: str,
                        temperature: float, max_tokens: int) -> RespuestaIA:
        respuesta = await self._cliente.chat.completions.create(
            model=modelo,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature, max_tokens=max_tokens,
            extra_body=self._extra_body,
        )
        eleccion = respuesta.choices[0]
        return RespuestaIA(contenido=eleccion.message.content or "", finish_reason=eleccion.finish_reason)


def crear_cliente_ia(config: Config) -> ClienteIA:
    if config.llm_provider == "azure_openai":
        # En Azure el "modelo" de cada fase es el nombre del despliegue.
        cliente = AsyncAzureOpenAI(
            azure_endpoint=config.llm_base_url, api_key=config.llm_api_key,
            api_version=config.llm_api_version,
        )
    else:
        cliente = AsyncOpenAI(base_url=config.llm_base_url, api_key=config.llm_api_key)
    return ClienteIA(cliente, extra_body=config.llm_extra_body)
