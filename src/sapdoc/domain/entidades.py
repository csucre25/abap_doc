"""Entidades que viajan entre capas: qué modelo usa cada fase, quién está
autorizado, y la entrada y el resultado de documentar una OT."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Modelos:
    """Modelo de IA a usar en cada fase."""
    fase0: str
    fase1: str
    fase1_5: str
    fase2: str
    fase4: str

@dataclass(frozen=True)
class Usuario:
    """Persona autorizada a usar el bot. Cada canal la identifica con su
    propio campo (hoy solo Telegram)."""
    telegram_id: int
    nombre: str
    rol: str = "usuario"


@dataclass
class EntradaPipeline:
    """Lo que el canal entrega al caso de uso, ya leído de los archivos subidos:
    metadata y fuentes ABAP del ZIP, texto de calibración y AFU, el .docx
    original del AFU y si se pidió el DTSS completo (técnico + funcional)."""
    metadata_json: dict
    abap_dict: dict
    calib_raw_text: str | None = None
    afu_raw_text: str | None = None
    afu_docx_bytes: bytes | None = None
    incluir_funcional: bool = False

@dataclass
class ResultadoPipeline:
    """Lo que el caso de uso devuelve al canal: el mensaje final, las rutas del
    Word y los CSV generados, el JSON estructurado y los avisos para el usuario."""
    mensaje: str = ""
    mensaje_html: bool = True
    docx_path: str | None = None
    csvs_configuracion: list = field(default_factory=list)
    borrador_json: str | None = None
    afu_json: dict = field(default_factory=dict)
    # Cosas que el usuario debe saber aunque el documento se haya generado
    # (texto plano, sin formato): el canal las muestra junto al mensaje final.
    avisos: list = field(default_factory=list)
