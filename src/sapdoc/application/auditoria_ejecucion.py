"""Registro de una ejecución: los prompts enviados y lo que respondió la IA en
cada fase. Se entrega al usuario como .txt junto al documento. (El registro
permanente de quién hizo qué es sapdoc.infrastructure.bitacora.)"""

from sapdoc.application.marcas_tiempo import get_current_timestamp


class RegistroAuditoria:
    """Acumula, fase por fase, el prompt enviado y la respuesta del LLM."""

    def __init__(self):
        self._entradas = []

    def agregar(self, phase: str, prompt: str, response: str):
        timestamp = get_current_timestamp()
        self._entradas.append(
            f"\n\n{'='*50}\n[{phase}] - {timestamp}\n{'='*50}\n"
            f"--- PROMPT ENVIADO ---\n{prompt}\n\n"
            f"--- RESPUESTA DEL LLM ---\n{response}\n"
        )

    def texto(self) -> str:
        return "".join(self._entradas) or "Sin logs registrados."
