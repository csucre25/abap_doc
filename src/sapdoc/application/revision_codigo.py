"""Fase 1.5 (opcional): Code Review del ABAP de la OT con IA."""

import json
import logging

from sapdoc.application.auditoria_ejecucion import RegistroAuditoria
from sapdoc.application.textos import TEXTS
from sapdoc.domain.respuestas_ia import clean_json_response

LANG = "es"
logger = logging.getLogger(__name__)


async def revisar_codigo(cliente_ia, modelo, metadata_str, abap_code_str, auditoria: RegistroAuditoria):
    """Fase 1.5: devuelve el dict del Code Review ({'has_violations': ..., 'findings': [...]})."""
    sys_prompt = TEXTS[LANG]["sys_prompt_fase1_5"]
    usr_prompt = TEXTS[LANG]["user_prompt_fase1_5"].format(metadata_json=metadata_str, codigo_abap=abap_code_str)

    response_fase1_5 = await cliente_ia.completar(
        modelo, sys_prompt, usr_prompt, temperature=0.1, max_tokens=1500,
    )

    review_txt = clean_json_response(response_fase1_5.contenido.strip())
    auditoria.agregar("FASE 1.5 - CODE REVIEW", sys_prompt, review_txt)

    try:
        return json.loads(review_txt.strip())
    except json.JSONDecodeError:
        logger.warning("Fallo al parsear JSON del Code Review. Ignorando validaciones.")
        # "ilegible" no cambia el flujo (sigue sin bloquear), pero deja constancia en la bitácora.
        return {"has_violations": False, "ilegible": True}
