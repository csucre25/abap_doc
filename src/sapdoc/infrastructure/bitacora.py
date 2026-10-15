# bitacora.py
#
# Registro de auditoría permanente, independiente del canal: un evento por
# línea en JSONL, solo se añade al final y cada línea lleva la huella (SHA-256)
# de la anterior. Si alguien borra, reordena o modifica una línea, la cadena
# deja de cuadrar y `sapdoc-verificar-auditoria <ruta>` lo señala.
#
# Nunca guarda el contenido de los documentos: solo metadatos y huellas, para
# poder demostrar qué se procesó sin conservar código ni datos del cliente.
#
# Ojo: la cadena detecta cambios, pero quien pueda escribir el archivo podría
# regenerarla entera. Para garantía frente a eso hay que enviar los eventos a
# un almacenamiento externo de solo-añadir.

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone

HASH_INICIAL = "0" * 64


def sha256_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def sha256_archivo(ruta: str) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def huella_archivo(nombre: str, datos: bytes) -> dict:
    """Lo que la bitácora guarda de un archivo: nombre, tamaño y huella."""
    return {"nombre": nombre, "bytes": len(datos), "sha256": sha256_bytes(datos)}


def _hash_evento(evento: dict) -> str:
    canonico = json.dumps(evento, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(canonico.encode("utf-8"))


class Bitacora:
    def __init__(self, ruta: str):
        self.ruta = ruta
        self._lock = threading.Lock()
        carpeta = os.path.dirname(ruta)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        self._ultimo_hash = self._leer_ultimo_hash()

    def _leer_ultimo_hash(self) -> str:
        if not os.path.exists(self.ruta):
            return HASH_INICIAL
        ultima = None
        with open(self.ruta, encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    ultima = linea
        return json.loads(ultima)["hash"] if ultima else HASH_INICIAL

    def registrar(self, tipo: str, **datos) -> dict:
        """Añade un evento y lo devuelve tal como quedó escrito."""
        with self._lock:
            evento = {
                "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "evento": tipo,
                **datos,
                "hash_anterior": self._ultimo_hash,
            }
            evento["hash"] = _hash_evento(evento)
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(json.dumps(evento, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._ultimo_hash = evento["hash"]
            return evento


def verificar(ruta: str):
    """Recorre la cadena. Devuelve (eventos_correctos, None) si está íntegra o
    (número_de_línea, motivo) en la primera línea que no cuadra."""
    anterior, n = HASH_INICIAL, 0
    with open(ruta, encoding="utf-8") as f:
        for numero, linea in enumerate(f, start=1):
            if not linea.strip():
                continue
            try:
                evento = json.loads(linea)
            except json.JSONDecodeError:
                return numero, "la línea no es JSON válido"
            guardado = evento.pop("hash", None)
            if evento.get("hash_anterior") != anterior:
                return numero, "no encadena con la línea anterior (¿se borró o reordenó una línea?)"
            if _hash_evento(evento) != guardado:
                return numero, "su contenido fue modificado"
            anterior, n = guardado, n + 1
    return n, None


def main_cli(argv=None):
    """Comando `sapdoc-verificar-auditoria <ruta.jsonl>`: 0 si la cadena está
    íntegra, 1 si está alterada, 2 si se usó mal."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Uso: sapdoc-verificar-auditoria <ruta.jsonl>")
        return 2
    posicion, motivo = verificar(argv[0])
    if motivo:
        print(f"ALTERADA en la línea {posicion}: {motivo}.")
        return 1
    print(f"Íntegra: {posicion} evento(s) encadenados correctamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
