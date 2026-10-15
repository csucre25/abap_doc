"""Tests del núcleo (pipeline.py) con una OT sintética y un cliente de IA falso."""
import asyncio
import json
import os
import subprocess
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from sapdoc.application.auditoria_ejecucion import RegistroAuditoria
from sapdoc.application.documentar_ot import ejecutar_pipeline
from sapdoc.application.puertos import RespuestaIA
from sapdoc.application.revision_codigo import revisar_codigo
from sapdoc.application.textos import TEXTS
from sapdoc.domain.clasificacion import clasificar_objetos_previos
from sapdoc.domain.entidades import EntradaPipeline, Modelos
from sapdoc.infrastructure.word import GeneradorWord
from sapdoc.infrastructure.zip_ot import ZipSinMetadataError, leer_zip_ot

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLA = RAIZ / "plantillas" / "plantilla_dtss.docx"
GENERADOR = GeneradorWord(str(PLANTILLA))
MODELOS = Modelos(fase0="modelo-f0", fase1="modelo-f1", fase1_5="modelo-cr", fase2="modelo-f2", fase4="modelo-f4")

METADATA = {
    "system_info": {"sid": "DEV", "s4core": "108"},
    "transport_request": {
        "id": "DEVK900001", "author": "DESARROLLADOR", "description": "OT de prueba", "date": "20261013",
    },
    "objects": [
        {"object_type": "PROG", "object_name": "ZR_DEMO", "files": {"active": "zr_demo.abap"}},
        {"object_type": "METH", "object_name": "ZCL_DEMO=> M_CALC",
         "files": {"active": "zcl_demo_m_calc.abap", "previous": "zcl_demo_m_calc_prev.abap"}},
        {"object_type": "TABL", "object_name": "ZT_DEMO", "short_text": "Tabla demo",
         "fields": [{"field_name": "MANDT", "type": "CLNT", "length": 3, "rollname": "MANDT"}]},
        {"object_type": "TABU", "object_name": "ZTCONSTANTES",
         "customizing": {"columns": ["CAMPO", "LOW"], "rows": [["GERENCIA", "0876"], ["MODO", "X"]]}},
        {"object_type": "TABU", "object_name": "TDDAT"},
        {"object_type": "FUNC", "object_name": "VIEWFRAME_ZV_DEMO", "files": {"active": "viewframe.abap"}},
    ],
}

ABAP = {
    "src/zr_demo.abap": "REPORT zr_demo.\nIF sy-subrc < 4 AND <fs_demo> IS ASSIGNED.\nENDIF.\n",
    "src/zcl_demo_m_calc.abap": "METHOD m_calc.\n  rv_total = iv_a + iv_b.\nENDMETHOD.\n",
    "src/zcl_demo_m_calc_prev.abap": "METHOD m_calc.\n  rv_total = iv_a.\nENDMETHOD.\n",
    "src/viewframe.abap": "FUNCTION viewframe_zv_demo.\nENDFUNCTION.\n",
}


class ClienteIAFalso:
    """Imita ia.ClienteIA: responde según la fase (la identifica por el system prompt)."""

    def __init__(self, respuesta_cr='{"has_violations": false, "findings": []}', respuesta_f0='{"tone": "Sobrio"}'):
        self.llamadas = []
        self.respuesta_cr = respuesta_cr
        self.respuesta_f0 = respuesta_f0

    async def completar(self, modelo, system, user, temperature, max_tokens):
        self.llamadas.append(modelo)
        if system == TEXTS["es"]["sys_prompt_fase1_5"]:
            contenido = self.respuesta_cr
        elif system == TEXTS["es"]["sys_prompt_fase0"]:
            contenido = self.respuesta_f0
        elif system == TEXTS["es"]["sys_prompt_fase2"]:
            # La IA "limpia" el nombre y cambia el tipo: el núcleo debe imponer los de metadata.
            contenido = json.dumps([{
                "object_name": "NOMBRE_INVENTADO", "object_type": "CLAS",
                "technical_mechanics": {"description": "Suma dos valores.", "ddic_fields": [{"field_name": "X"}]},
            }])
        else:
            contenido = json.dumps({
                "redacciones": [
                    {"nombre": "ZR_DEMO", "justificacion": "Valor de negocio.", "logica_tecnica": "Resumen."},
                ],
            })
        return RespuestaIA(contenido=contenido, finish_reason="stop")


@pytest.fixture
def zip_ot(tmp_path):
    ruta = tmp_path / "ot.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("metadata.json", json.dumps(METADATA))
        for nombre, codigo in ABAP.items():
            z.writestr(nombre, codigo)
    return ruta


def test_leer_zip_ot_devuelve_metadata_y_codigo_por_nombre_de_archivo(zip_ot):
    metadata, abap_dict = leer_zip_ot(zip_ot)
    assert metadata == METADATA
    assert set(abap_dict) == {os.path.basename(n) for n in ABAP}
    assert abap_dict["zr_demo.abap"] == ABAP["src/zr_demo.abap"]


def test_leer_zip_ot_sin_metadata_falla(tmp_path):
    ruta = tmp_path / "sin_meta.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("zr_demo.abap", "REPORT zr_demo.")
    with pytest.raises(ZipSinMetadataError):
        leer_zip_ot(ruta)


def test_leer_zip_ot_conserva_el_codigo_que_no_es_utf8(tmp_path):
    # Antes el segundo read() del stream ya consumido devolvía b"" y el fuente quedaba vacío.
    codigo = "REPORT zr_año.\n* Descripción con tildes\n"
    ruta = tmp_path / "latin1.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("metadata.json", json.dumps(METADATA))
        z.writestr("src/zr_ano.abap", codigo.encode("latin-1"))
    _, abap_dict = leer_zip_ot(ruta)
    assert abap_dict["zr_ano.abap"] == codigo


def test_clasificacion_solo_manda_a_ia_los_objetos_con_logica():
    ruido, diccionario, logica = clasificar_objetos_previos(METADATA)
    assert [o["object_name"] for o in logica] == ["ZR_DEMO", "ZCL_DEMO=> M_CALC"]
    assert [o["object_name"] for o in diccionario] == ["ZT_DEMO", "ZTCONSTANTES"]
    assert [o["object_name"] for o in ruido] == ["TDDAT", "VIEWFRAME_ZV_DEMO"]


def test_ejecutar_pipeline_genera_documento_sin_canal(zip_ot, tmp_path, monkeypatch):
    monkeypatch.chdir(RAIZ)  # la plantilla se resuelve relativa a la raíz
    metadata, abap_dict = leer_zip_ot(zip_ot)
    cliente = ClienteIAFalso()
    auditoria = RegistroAuditoria()
    fases_mostradas = []

    @asynccontextmanager
    async def progreso(texto):
        fases_mostradas.append(texto)
        yield

    salida = tmp_path / "salida"
    salida.mkdir()
    resultado = asyncio.run(ejecutar_pipeline(
        EntradaPipeline(metadata_json=metadata, abap_dict=abap_dict),
        cliente, MODELOS, auditoria, GENERADOR, progreso=progreso, dir_salida=str(salida),
    ))

    assert resultado.mensaje_html and "Documento Técnico generado" in resultado.mensaje
    assert resultado.docx_path and os.path.getsize(resultado.docx_path) > 0
    assert Path(resultado.docx_path).parent == salida

    # Sin calibración ni AFU no se llama a esas fases: 2 objetos con lógica + 1 redacción.
    assert cliente.llamadas == ["modelo-f2", "modelo-f2", "modelo-f4"]
    assert fases_mostradas == [TEXTS["es"]["zip_fase_paralela"], TEXTS["es"]["zip_fase4"]]

    borrador = json.loads(resultado.borrador_json)
    logica = {o["nombre"]: o for o in borrador["objetos_logica"]}
    assert set(logica) == {"ZR_DEMO", "ZCL_DEMO=> M_CALC"}  # nombres de metadata, no los de la IA
    assert logica["ZR_DEMO"]["tipo"] == "PROG"
    assert logica["ZR_DEMO"]["justificacion"] == "Valor de negocio."
    assert logica["ZR_DEMO"]["codigo"]["nuevo"] == ABAP["src/zr_demo.abap"]
    assert logica["ZR_DEMO"]["campos"] == []  # un PROG no tiene campos DDIC propios
    assert logica["ZCL_DEMO=> M_CALC"]["codigo"] == {
        "nuevo": "  rv_total = iv_a + iv_b.", "anterior": "  rv_total = iv_a.",
    }
    assert [o["nombre"] for o in borrador["objetos_diccionario"]] == ["ZT_DEMO"]
    assert [o["nombre"] for o in borrador["configuracion"]] == ["ZTCONSTANTES"]
    assert [o["nombre"] for o in borrador["objetos_omitidos_ruido"]] == ["TDDAT", "VIEWFRAME_ZV_DEMO"]
    assert "constantes" not in borrador  # ZTCONSTANTES va en "configuracion", no hay sección aparte

    assert [Path(c).name for c in resultado.csvs_configuracion] == ["ZTCONSTANTES_customizing.csv"]
    assert Path(resultado.csvs_configuracion[0]).read_text(encoding="utf-8").splitlines() == [
        "CAMPO,LOW", "GERENCIA,0876", "MODO,X",
    ]

    log = auditoria.texto()
    for fase in ("FASE 0 - CALIBRACIÓN", "FASE 1 - AFU", "FASE 2 - ABAP", "FASE 4 - TEXTOS", "FASE 5 - ARMADO DOCX"):
        assert fase in log


def test_cada_fase_usa_su_modelo_y_la_plantilla_indicada(tmp_path):
    cliente = ClienteIAFalso()
    auditoria = RegistroAuditoria()
    resultado = asyncio.run(ejecutar_pipeline(
        EntradaPipeline(metadata_json=METADATA, abap_dict={}, calib_raw_text="estilo", afu_raw_text="requisitos"),
        cliente, MODELOS, auditoria, GENERADOR, dir_salida=str(tmp_path),
    ))
    assert sorted(cliente.llamadas) == ["modelo-f0", "modelo-f1", "modelo-f2", "modelo-f2", "modelo-f4"]
    assert resultado.docx_path and os.path.exists(resultado.docx_path)
    assert f"plantilla={PLANTILLA}" in auditoria.texto()


def _correr_con_calibracion(tmp_path, calib_raw_text, respuesta_f0='{"tone": "Sobrio"}'):
    cliente = ClienteIAFalso(respuesta_f0=respuesta_f0)
    auditoria = RegistroAuditoria()
    resultado = asyncio.run(ejecutar_pipeline(
        EntradaPipeline(metadata_json=METADATA, abap_dict={}, calib_raw_text=calib_raw_text),
        cliente, MODELOS, auditoria, GENERADOR, dir_salida=str(tmp_path),
    ))
    assert resultado.docx_path  # el documento se genera en los tres casos
    return resultado, auditoria.texto(), cliente


def test_calibracion_usada_no_genera_avisos(tmp_path):
    resultado, log, cliente = _correr_con_calibracion(tmp_path, "documento histórico")
    assert resultado.avisos == []
    assert "Tono requerido: Sobrio" in log
    assert "FASE 0 OMITIDA" not in log and "FASE 0 FALLIDA" not in log


def test_calibracion_saltada_por_el_usuario_no_avisa(tmp_path):
    resultado, log, cliente = _correr_con_calibracion(tmp_path, None)
    assert resultado.avisos == []
    assert "FASE 0 OMITIDA" in log
    assert "modelo-f0" not in cliente.llamadas


def test_calibracion_con_respuesta_ilegible_de_la_ia_se_avisa(tmp_path):
    # Caso real: el modelo de la fase 0 respondió vacío y el log decía "OMITIDA".
    resultado, log, cliente = _correr_con_calibracion(tmp_path, "documento histórico", respuesta_f0="")
    assert resultado.avisos == [TEXTS["es"]["aviso_calib_fallida"]]
    assert "FASE 0 FALLIDA: El modelo modelo-f0" in log and "FASE 0 OMITIDA" not in log
    assert "modelo-f0" in cliente.llamadas


def test_calibracion_sin_texto_extraible_se_avisa(tmp_path):
    resultado, log, cliente = _correr_con_calibracion(tmp_path, "  \n")
    assert resultado.avisos == [TEXTS["es"]["aviso_calib_sin_texto"]]
    assert "FASE 0 SIN TEXTO" in log
    assert "modelo-f0" not in cliente.llamadas


def test_ejecutar_pipeline_no_levanta_si_falla_la_ia(tmp_path):
    class ClienteRoto(ClienteIAFalso):
        async def completar(self, modelo, system, user, temperature, max_tokens):
            if modelo == "modelo-f4":
                raise RuntimeError("servicio caído")
            return await super().completar(modelo, system, user, temperature, max_tokens)

    auditoria = RegistroAuditoria()
    resultado = asyncio.run(ejecutar_pipeline(
        EntradaPipeline(metadata_json=METADATA, abap_dict={}),
        ClienteRoto(), MODELOS, auditoria, GENERADOR, dir_salida=str(tmp_path),
    ))
    assert not resultado.mensaje_html
    assert "servicio caído" in resultado.mensaje
    assert resultado.docx_path is None and resultado.borrador_json is None
    assert "FASE 2 - ABAP" in auditoria.texto()  # lo ya registrado no se pierde
    assert "ERROR - PIPELINE INTERRUMPIDO" in auditoria.texto()
    assert "RuntimeError: servicio caído" in auditoria.texto()


class ClienteFase2Vacia(ClienteIAFalso):
    """La Fase 2 responde una lista vacía para ZR_DEMO; el resto, como el falso normal."""

    async def completar(self, modelo, system, user, temperature, max_tokens):
        if system == TEXTS["es"]["sys_prompt_fase2"] and '"ZR_DEMO"' in user:
            self.llamadas.append(modelo)
            return RespuestaIA(contenido="[]", finish_reason="stop")
        return await super().completar(modelo, system, user, temperature, max_tokens)


class ClienteFase2Caida(ClienteIAFalso):
    """La Fase 2 falla siempre para ZR_DEMO (se agotan los reintentos)."""

    async def completar(self, modelo, system, user, temperature, max_tokens):
        if system == TEXTS["es"]["sys_prompt_fase2"] and '"ZR_DEMO"' in user:
            self.llamadas.append(modelo)
            raise RuntimeError("servicio caído")
        return await super().completar(modelo, system, user, temperature, max_tokens)


@pytest.mark.parametrize("cliente", [ClienteFase2Vacia(), ClienteFase2Caida()], ids=["lista_vacia", "reintentos_agotados"])
def test_objeto_sin_analisis_de_fase2_sigue_en_el_documento(cliente, tmp_path, monkeypatch):
    # Antes, "[]" (respuesta vacía o reintentos agotados) hacía desaparecer el objeto del DTSS.
    async def sin_espera(_segundos):
        pass
    monkeypatch.setattr("sapdoc.application.documentar_ot.asyncio.sleep", sin_espera)

    auditoria = RegistroAuditoria()
    resultado = asyncio.run(ejecutar_pipeline(
        EntradaPipeline(metadata_json=METADATA, abap_dict={os.path.basename(k): v for k, v in ABAP.items()}),
        cliente, MODELOS, auditoria, GENERADOR, dir_salida=str(tmp_path),
    ))

    logica = {o["nombre"]: o for o in json.loads(resultado.borrador_json)["objetos_logica"]}
    assert set(logica) == {"ZR_DEMO", "ZCL_DEMO=> M_CALC"}
    assert logica["ZR_DEMO"]["tipo"] == "PROG"
    assert logica["ZR_DEMO"]["codigo"]["nuevo"] == ABAP["src/zr_demo.abap"]
    assert "sin análisis de IA por JSON inválido tras reintentos: ZR_DEMO" in auditoria.texto()


def test_revisar_codigo_devuelve_hallazgos_y_los_audita():
    cliente = ClienteIAFalso('```json\n{"has_violations": true, "findings": [{"description": "SELECT *"}]}\n```')
    auditoria = RegistroAuditoria()
    review = asyncio.run(revisar_codigo(cliente, MODELOS.fase1_5, "{}", "REPORT z.", auditoria))
    assert review["has_violations"] is True
    assert review["findings"][0]["description"] == "SELECT *"
    assert cliente.llamadas == ["modelo-cr"]
    assert "FASE 1.5 - CODE REVIEW" in auditoria.texto()


def test_revisar_codigo_con_respuesta_ilegible_no_reporta_violaciones():
    # Comportamiento vigente (falla en abierto); se endurecerá por configuración.
    review = asyncio.run(revisar_codigo(
        ClienteIAFalso("no pude analizarlo"), MODELOS.fase1_5, "{}", "", RegistroAuditoria()))
    assert review == {"has_violations": False, "ilegible": True}


def test_registro_auditoria_vacio():
    assert RegistroAuditoria().texto() == "Sin logs registrados."


def test_el_nucleo_no_depende_de_telegram():
    codigo = ("import sys, sapdoc.application.documentar_ot; "
              "sys.exit(1 if any(m.split('.')[0] == 'telegram' for m in sys.modules) else 0)")
    proceso = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True,
                             env={**os.environ, "PYTHONPATH": str(RAIZ / "src")})
    assert proceso.returncode == 0, proceso.stderr


def test_la_redaccion_no_pide_constantes_a_la_ia():
    # Se pedían constantes de código a la IA y las inventaba (copiaba el ejemplo del prompt).
    assert "constantes" not in TEXTS["es"]["sys_prompt_fase4"].lower()
    assert "GC_MODE" not in TEXTS["es"]["sys_prompt_fase4"]
