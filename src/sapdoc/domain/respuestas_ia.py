"""Cómo se extrae y repara el JSON que devuelven los modelos de IA. Son
reglas de texto puras: no llaman a ningún modelo."""

import json
import re


def clean_json_response(raw_text: str) -> str:
    """Extrae de forma segura el JSON oculto dentro de texto conversacional."""
    text = raw_text.strip()
    
    # 1. Intentar extraer por bloques markdown
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            
    # 2. Limpieza extrema: buscar la primera llave o corchete
    text = text.strip()
    start_idx = text.find('{')
    start_idx_array = text.find('[')
    
    # Encontrar cuál empieza primero (objeto o array)
    if start_idx != -1 and start_idx_array != -1:
        start_idx = min(start_idx, start_idx_array)
    elif start_idx == -1:
        start_idx = start_idx_array
        
    if start_idx != -1:
        text = text[start_idx:] # Cortar basura inicial
        # Buscar el último cierre
        end_idx = max(text.rfind('}'), text.rfind(']'))
        if end_idx != -1:
            text = text[:end_idx+1] # Cortar basura final
            
    return text.strip()


# La IA a veces repite la clave dentro del valor: "object_name": "object_name": "ZOBJ"
# (JSON inválido). Es un defecto de generación, no de contenido: se puede reparar sin perder datos.
_RE_CLAVE_DUPLICADA = re.compile(r'("(\w+)"\s*:\s*)"\2"\s*:\s*')

_RE_COMA_FINAL = re.compile(r',\s*([}\]])')


def parsear_json_lote(texto_crudo):
    """Devuelve la lista del lote de Fase 2, o None si no se puede leer ni reparando
    los defectos conocidos (clave repetida, coma antes de } o ])."""
    limpio = clean_json_response(texto_crudo or "")
    reparado = _RE_COMA_FINAL.sub(r'\1', _RE_CLAVE_DUPLICADA.sub(r'\1', limpio))
    for candidato in (limpio, reparado):
        try:
            data = json.loads(candidato)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
    return None
