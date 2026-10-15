"""Registro de auditoría encadenado (bitacora.py) y su uso desde el bot."""
import json
import os
import subprocess
import sys
from pathlib import Path

from sapdoc.domain.entidades import ResultadoPipeline
from sapdoc.infrastructure.bitacora import HASH_INICIAL, Bitacora, huella_archivo, sha256_bytes, verificar
from sapdoc.interfaces.telegram import bot

RAIZ = Path(__file__).resolve().parent.parent


def _lineas(ruta):
    return [json.loads(l) for l in Path(ruta).read_text(encoding="utf-8").splitlines() if l.strip()]


def test_cada_evento_encadena_con_el_anterior(tmp_path):
    ruta = tmp_path / "sub" / "eventos.jsonl"  # crea la carpeta si no existe
    b = Bitacora(str(ruta))
    e1 = b.registrar("prueba", dato=1)
    e2 = b.registrar("prueba", dato="ñandú")
    assert e1["hash_anterior"] == HASH_INICIAL and e2["hash_anterior"] == e1["hash"]
    assert _lineas(ruta) == [e1, e2]
    assert verificar(str(ruta)) == (2, None)


def test_al_reabrir_continua_la_cadena(tmp_path):
    ruta = str(tmp_path / "eventos.jsonl")
    e1 = Bitacora(ruta).registrar("a")
    e2 = Bitacora(ruta).registrar("b")
    assert e2["hash_anterior"] == e1["hash"]
    assert verificar(ruta) == (2, None)


def test_detecta_una_linea_modificada(tmp_path):
    ruta = tmp_path / "eventos.jsonl"
    b = Bitacora(str(ruta))
    for i in range(3):
        b.registrar("evento", n=i)
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    lineas[1] = lineas[1].replace('"n": 1', '"n": 99')
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    assert verificar(str(ruta)) == (2, "su contenido fue modificado")


def test_detecta_una_linea_borrada(tmp_path):
    ruta = tmp_path / "eventos.jsonl"
    b = Bitacora(str(ruta))
    for i in range(3):
        b.registrar("evento", n=i)
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    del lineas[1]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    numero, motivo = verificar(str(ruta))
    assert numero == 2 and "no encadena" in motivo


def test_verificacion_desde_la_linea_de_comandos(tmp_path):
    ruta = tmp_path / "eventos.jsonl"
    Bitacora(str(ruta)).registrar("a")
    proceso = subprocess.run([sys.executable, "-m", "sapdoc.infrastructure.bitacora", str(ruta)],
                             capture_output=True, text=True, encoding="utf-8", cwd=RAIZ,
                             env={**os.environ, "PYTHONPATH": str(RAIZ / "src")})
    assert proceso.returncode == 0 and "Íntegra: 1" in proceso.stdout


def test_huella_de_archivo_no_guarda_contenido():
    assert huella_archivo("ot.zip", b"abc") == {"nombre": "ot.zip", "bytes": 3, "sha256": sha256_bytes(b"abc")}


def test_fin_de_ejecucion_registra_huellas_de_lo_generado(tmp_path):
    docx = tmp_path / "DTSS.docx"
    docx.write_bytes(b"word")
    csv = tmp_path / "ZT_customizing.csv"
    csv.write_bytes(b"A,B\n")  # bytes exactos: write_text cambiaría \n por \r\n en Windows
    b = Bitacora(str(tmp_path / "eventos.jsonl"))
    resultado = ResultadoPipeline(mensaje="ok", docx_path=str(docx), csvs_configuracion=[str(csv)],
                                  borrador_json="{}", avisos=["aviso"])
    bot.registrar_fin_ejecucion(b, "id1", resultado, 12.34)
    (evento,) = _lineas(b.ruta)
    assert evento["evento"] == "ejecucion_finalizada" and evento["estado"] == "documento_generado"
    assert evento["salidas"] == {
        "docx_sha256": sha256_bytes(b"word"), "json_sha256": sha256_bytes(b"{}"),
        "csv_sha256": {"ZT_customizing.csv": sha256_bytes(b"A,B\n")},
    }
    assert evento["avisos"] == ["aviso"] and evento["duracion_s"] == 12.3 and evento["error"] is None


def test_fin_de_ejecucion_fallida_guarda_el_motivo(tmp_path):
    b = Bitacora(str(tmp_path / "eventos.jsonl"))
    bot.registrar_fin_ejecucion(b, "id2", ResultadoPipeline(mensaje="Detalle: sin saldo", mensaje_html=False), 1)
    bot.registrar_fin_ejecucion(b, "id3", ResultadoPipeline(mensaje="falló el Word", borrador_json="{}"), 1)
    bot.registrar_fin_ejecucion(b, "id4", None, 1)
    fallida, solo_json, sin_resultado = _lineas(b.ruta)
    assert (fallida["estado"], fallida["error"]) == ("error", "Detalle: sin saldo")
    assert solo_json["estado"] == "solo_json" and solo_json["error"] is None
    assert sin_resultado["estado"] == "error"
