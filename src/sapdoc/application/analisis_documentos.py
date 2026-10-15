"""Fases 0 y 1: extraer con IA el estilo del documento de calibración y los
requerimientos del documento funcional (AFU)."""

import json
import logging

from sapdoc.application.textos import TEXTS
from sapdoc.domain.respuestas_ia import clean_json_response

LANG = "es"
logger = logging.getLogger(__name__)


async def analyze_functional_text(raw_text: str, cliente_ia, modelo: str) -> dict:
    """Llama a la IA para analizar el texto funcional."""
    system_prompt = TEXTS[LANG]["sys_prompt_fase1"]
    user_prompt = TEXTS[LANG]["user_prompt_fase1"].format(texto_del_documento=raw_text)

    response = await cliente_ia.completar(modelo, system_prompt, user_prompt, temperature=0.0, max_tokens=6000)

    cleaned_response = clean_json_response(response.contenido)
    try:
        return json.loads(cleaned_response)
    except json.JSONDecodeError as e:
        print(f"Error parseando AFU JSON: {e}\nTexto limpio: {cleaned_response[:100]}...")
        return {
            "document_type": "Desconocido",
            "general_objective": "Error de parseo IA",
            "requirements": [],
            "_debug_raw_response": cleaned_response[:1500],  # visible en el audit_log de Telegram
        }


async def analyze_calibration_text(raw_text: str, cliente_ia, modelo: str) -> dict:
    """Llama a la IA para analizar el texto de calibración."""
    system_prompt = TEXTS[LANG]["sys_prompt_fase0"]
    user_prompt = TEXTS[LANG]["user_prompt_fase0"].format(texto_historico=raw_text)

    response = await cliente_ia.completar(modelo, system_prompt, user_prompt, temperature=0.0, max_tokens=2048)

    contenido = response.contenido
    cleaned_response = clean_json_response(contenido)
    try:
        return json.loads(cleaned_response)
    except json.JSONDecodeError as e:
        # finish_reason y longitud distinguen "respondió vacío" de "se cortó por max_tokens".
        logger.warning(
            f"Error parseando Calibración JSON ({e}). modelo={modelo} "
            f"finish_reason={response.finish_reason} caracteres_recibidos={len(contenido)} "
            f"inicio={cleaned_response[:100]!r}"
        )
        return {}
