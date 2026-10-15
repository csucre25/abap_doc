"""Generación del Word (DTSS): con la plantilla propia (plantilla.py) o
extendiendo el AFU que subió el usuario (afu.py)."""

from sapdoc.infrastructure.word.afu import renderizar_documento_con_afu
from sapdoc.infrastructure.word.plantilla import (
    PLANTILLA_PATH, construir_contexto, exportar_csv_configuracion, renderizar_documento,
)


class GeneradorWord:
    """Implementa el puerto GeneradorDocumento con python-docx/docxtpl."""

    def __init__(self, plantilla_path: str = PLANTILLA_PATH):
        """Guarda la plantilla a usar en el modo técnico."""
        self.plantilla_path = plantilla_path

    def construir_contexto(self, dtss_final, metadata_json, cliente="Cliente", proyecto="Proyecto",
                           escapar_xml=True):
        """Convierte el contenido generado (dtss_final) en las variables de la plantilla."""
        return construir_contexto(dtss_final, metadata_json, cliente=cliente, proyecto=proyecto,
                                  escapar_xml=escapar_xml)

    def renderizar(self, contexto, output_path, imagenes_tmp_dir):
        """DTSS técnico: rellena la plantilla y guarda el .docx en output_path."""
        return renderizar_documento(contexto, output_path, plantilla_path=self.plantilla_path,
                                    imagenes_tmp_dir=imagenes_tmp_dir)

    def renderizar_con_afu(self, contexto, afu_docx_path, output_path, imagenes_tmp_dir):
        """DTSS completo: añade las secciones técnicas al final del AFU del usuario."""
        return renderizar_documento_con_afu(contexto, afu_docx_path, output_path, imagenes_tmp_dir)

    def exportar_csv(self, cfg, output_dir):
        """Exporta la tabla de customizing completa a CSV y devuelve su ruta."""
        return exportar_csv_configuracion(cfg, output_dir)
