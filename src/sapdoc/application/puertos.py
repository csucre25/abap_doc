"""Puertos: lo que la aplicación necesita del exterior, sin saber quién lo
implementa. La infraestructura los cumple (ver sapdoc.infrastructure.ia y
sapdoc.infrastructure.word) y los tests los sustituyen por dobles."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RespuestaIA:
    """Respuesta de un modelo, independiente del proveedor."""
    contenido: str        # nunca None: una respuesta sin texto llega como ""
    finish_reason: str | None = None


class ClienteIA(Protocol):
    """Cualquier proveedor de IA capaz de completar un par de prompts."""

    async def completar(self, modelo: str, system: str, user: str,
                        temperature: float, max_tokens: int) -> RespuestaIA:
        """Envía el prompt de sistema y el de usuario al modelo indicado."""
        ...


class DocxBuildError(Exception):
    """Envuelve cualquier falla al armar el .docx con una traza completa:
    traceback original + resumen del contexto usado + variables que la
    plantilla espera vs las que realmente se le mandaron. Pensado para
    volcarse tal cual al log de auditoría, no solo mostrarse como str(e)."""
    def __init__(self, trace_text: str):
        """Guarda la traza completa para volcarla al registro de auditoría."""
        self.trace_text = trace_text
        super().__init__(trace_text)


class GeneradorDocumento(Protocol):
    """Arma el Word final a partir del contenido ya generado."""

    plantilla_path: str

    def construir_contexto(self, dtss_final: dict, metadata_json: dict, cliente: str,
                           proyecto: str, escapar_xml: bool) -> dict:
        """Convierte el contenido generado en las variables del documento."""
        ...

    def renderizar(self, contexto: dict, output_path: str, imagenes_tmp_dir: str) -> str:
        """DTSS técnico a partir de la plantilla. Devuelve la ruta del .docx."""
        ...

    def renderizar_con_afu(self, contexto: dict, afu_docx_path: str, output_path: str,
                           imagenes_tmp_dir: str) -> str:
        """DTSS completo: el AFU del usuario más las secciones técnicas."""
        ...

    def exportar_csv(self, cfg: dict, output_dir: str) -> str:
        """Exporta una tabla de customizing a CSV. Devuelve la ruta del archivo."""
        ...
