"""Caso de uso principal: documentar una orden de transporte.

Recibe una EntradaPipeline (lo que el usuario subió, ya leído), orquesta las
fases de IA y arma el Word mediante el generador inyectado. No conoce el canal
(Telegram, Teams...) ni el proveedor de IA concreto."""

import asyncio
import functools
import json
import logging
import os
import tempfile
import traceback
from contextlib import asynccontextmanager

from sapdoc.application.analisis_documentos import analyze_calibration_text, analyze_functional_text
from sapdoc.application.auditoria_ejecucion import RegistroAuditoria
from sapdoc.application.marcas_tiempo import get_file_timestamp
from sapdoc.application.puertos import ClienteIA, DocxBuildError, GeneradorDocumento
from sapdoc.application.textos import TEXTS
from sapdoc.domain.clasificacion import (
    clasificar_objetos_previos, obtener_codigo_objeto, objeto_diccionario_desde_metadata,
    objeto_ruido_desde_metadata,
)
from sapdoc.domain.entidades import EntradaPipeline, Modelos, ResultadoPipeline
from sapdoc.domain.respuestas_ia import clean_json_response, parsear_json_lote

LANG = "es"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _sin_progreso(texto_base):
    """Progreso por defecto: no muestra nada (útil en tests o canales sin indicador)."""
    yield


def create_processing_batches(objects, system_info, abap_dict, max_chars_per_batch=15000):
    """Arma lotes SOLO con los objetos que ya se decidió mandar a Fase 2
    (objetos con lógica propia) — el pre-filtro de ruido/diccionario ya se
    aplicó en clasificar_objetos_previos() antes de llamar a esta función.

    TODOS los tipos con lógica van SIEMPRE en su propio lote (1 objeto = 1
    llamada a la IA), no solo PROG/CLAS/ENHO/FUNC/FUGR/REPS. Antes, tipos
    como METH o CLAS_SECTION se agrupaban varios en un mismo lote, y se
    detectó que la IA a veces: (a) "limpia" nombres de objeto con
    caracteres raros (ej. "NOMBRE=> METODO" pierde el espacio), rompiendo
    el cruce por nombre contra metadata.json, o (b) directamente fusiona
    2-3 objetos pequeños en una sola descripción, perdiendo la
    correspondencia 1 a 1. Ir siempre solo por objeto cuesta más llamadas
    a la IA, pero permite que ejecutar_pipeline ignore lo que la IA diga en
    'object_name'/'object_type' y use directo el valor real de
    metadata.json (ver 'obj_unico' en cada tupla devuelta).

    Devuelve una lista de (meta_json_str, abap_str, obj_unico) — obj_unico
    es el dict original del objeto cuando el lote trae exactamente uno
    (que ahora es siempre el caso), o None si en algún escenario futuro
    volviera a agruparse más de uno."""
    batches = []

    for obj in objects:
        active_file = obj.get("files", {}).get("active")
        obj_abap_text = ""
        if active_file and active_file in abap_dict:
            obj_abap_text = f"--- ARCHIVO: {active_file} ---\n{abap_dict[active_file]}\n\n"

        solo_meta = {"system_info": system_info, "objects": [obj]}
        batches.append((json.dumps(solo_meta, indent=2), obj_abap_text, obj))

    return batches


async def task_phase_0(raw_text, cliente_ia, modelo):
    """Fase 0: extrae el estilo de redacción del documento de calibración.
    Devuelve {} si no hay texto (calibración omitida o documento sin texto)."""
    if not (raw_text or "").strip(): return {}
    return await analyze_calibration_text(raw_text, cliente_ia, modelo)

async def task_phase_1(raw_text, cliente_ia, modelo):
    """Fase 1: extrae objetivo y requerimientos del documento funcional (AFU).
    Devuelve {} si no se subió AFU."""
    if not raw_text: return {}
    return await analyze_functional_text(raw_text, cliente_ia, modelo)

async def task_phase_2(meta_str, abap_str, semaforo, delay_offset, cliente_ia, modelo):
    """Fase 2: analiza con IA un lote (un objeto con lógica) y devuelve el texto crudo.

    Espera 'delay_offset' segundos y respeta el semáforo para no saturar al
    proveedor. Reintenta si la respuesta no es un JSON legible o si la llamada
    falla; tras el último intento devuelve "[]" en vez de propagar el error."""
    await asyncio.sleep(delay_offset)
    async with semaforo:
        max_retries = 3
        base_delay = 2
        for attempt in range(max_retries):
            try:
                response = await cliente_ia.completar(
                    modelo,
                    TEXTS[LANG]["sys_prompt_fase2"],
                    TEXTS[LANG]["user_prompt_fase2"].format(metadata_json=meta_str, codigo_abap=abap_str),
                    temperature=0.2,
                    max_tokens=3500,
                )
                contenido = response.contenido
                if parsear_json_lote(contenido) is None and attempt < max_retries - 1:
                    logger.warning(f"Fase 2: JSON inválido (Intento {attempt + 1}/{max_retries}); se reintenta el lote.")
                    await asyncio.sleep(base_delay)
                    continue
                return contenido
            except Exception as e:
                logger.warning(f"Error en Fase 2 (Intento {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    logger.error("Se agotaron los reintentos para este lote.")
                    return "[]"
                await asyncio.sleep(base_delay * (2 ** attempt))


async def ejecutar_pipeline(entrada: EntradaPipeline, cliente_ia: ClienteIA, modelos: Modelos,
                            auditoria: RegistroAuditoria, generador: GeneradorDocumento,
                            progreso=_sin_progreso, dir_salida: str = None) -> ResultadoPipeline:
    """Corre las fases 0, 1, 2 y 4 y arma el Word. Nunca levanta excepción:
    cualquier falla queda descrita en resultado.mensaje, con lo que se haya
    alcanzado a generar (el registro de auditoría siempre queda en 'auditoria').

    'progreso' es una función texto -> async context manager; el canal la usa
    para mostrar avance mientras dura cada fase de IA."""
    dir_salida = dir_salida or tempfile.gettempdir()
    resultado = ResultadoPipeline()

    try:
        # FASE 0, 1 Y 2 (Paralelo)
        async with progreso(TEXTS[LANG]["zip_fase_paralela"]):
            metadata_json = entrada.metadata_json
            ruido_objs, diccionario_objs, logica_objs = clasificar_objetos_previos(metadata_json)
            system_info = metadata_json.get('system_info', {})
            batches = create_processing_batches(logica_objs, system_info, entrada.abap_dict)
            semaforo = asyncio.Semaphore(3)
            tareas = [
                asyncio.create_task(task_phase_0(entrada.calib_raw_text, cliente_ia, modelos.fase0)),
                asyncio.create_task(task_phase_1(entrada.afu_raw_text, cliente_ia, modelos.fase1))
            ]
            batch_obj_unicos = []  # paralelo a las tareas de Fase 2, mismo orden
            for index, (meta_str, abap_str, obj_unico) in enumerate(batches):
                delay_offset = index * 0.5
                tareas.append(asyncio.create_task(
                    task_phase_2(meta_str, abap_str, semaforo, delay_offset, cliente_ia, modelos.fase2)))
                batch_obj_unicos.append(obj_unico)

            resultados = await asyncio.gather(*tareas)

        if resultados[0]:
            calib_json = resultados[0]
            log_prompt_fase0 = TEXTS[LANG]["sys_prompt_fase0"]
        else:
            # Tres motivos distintos para caer al perfil por defecto; solo el
            # primero es decisión del usuario, los otros dos hay que avisarlos.
            calib_json = TEXTS[LANG]["default_calibration"]
            if entrada.calib_raw_text is None:
                log_prompt_fase0 = "FASE 0 OMITIDA: Se aplicó el perfil corporativo por defecto del sistema."
            elif not entrada.calib_raw_text.strip():
                log_prompt_fase0 = (
                    "FASE 0 SIN TEXTO: El documento de calibración no contiene texto extraíble "
                    "(p. ej. un PDF escaneado). Se aplicó el perfil corporativo por defecto del sistema."
                )
                resultado.avisos.append(TEXTS[LANG]["aviso_calib_sin_texto"])
            else:
                log_prompt_fase0 = (
                    f"FASE 0 FALLIDA: El modelo {modelos.fase0} no devolvió un JSON válido para el documento "
                    "de calibración. Se aplicó el perfil corporativo por defecto del sistema."
                )
                resultado.avisos.append(TEXTS[LANG]["aviso_calib_fallida"])

        afu_json = resultados[1] or {}

        analisis_abap_consolidado = []
        objetos_sin_analisis = []
        for f2_result, obj_unico in zip(resultados[2:], batch_obj_unicos):
            lote_array = parsear_json_lote(f2_result)
            if lote_array is None:
                # Lote ilegible incluso tras reintentos y reparación. Antes el objeto
                # DESAPARECÍA del documento (dtss_final se arma desde este listado).
                # Ahora queda con su código y sin descripción de IA, y se avisa en el log.
                logger.warning(f"Error parseando lote ABAP: {clean_json_response(f2_result or '')[:50]}...")
                if obj_unico is None:
                    continue
                lote_array = [{
                    "object_name": obj_unico.get("object_name", ""),
                    "object_type": obj_unico.get("object_type", ""),
                    "executive_summary": "",
                    "technical_mechanics": {
                        "description": "Sin análisis automático disponible; ver código fuente.",
                        "ddic_fields": [],
                    },
                }]
                objetos_sin_analisis.append(obj_unico.get("object_name", ""))
            # El lote es de UN solo objeto (ver create_processing_batches):
            # no confiar en que la IA repita 'object_name'/'object_type'
            # tal cual — a veces "limpia" espacios raros en nombres como
            # "NOMBRE=> METODO" o cambia el tipo reportado, rompiendo el
            # cruce por nombre contra metadata.json más adelante. Se
            # fuerzan los valores reales conocidos de antemano.
            if obj_unico is not None and len(lote_array) == 1:
                lote_array[0]["object_name"] = obj_unico.get("object_name", "")
                lote_array[0]["object_type"] = obj_unico.get("object_type", "")
            analisis_abap_consolidado.extend(lote_array)

        analisis_abap = json.dumps(analisis_abap_consolidado, indent=2, ensure_ascii=False)

        auditoria.agregar("FASE 0 - CALIBRACIÓN", log_prompt_fase0, json.dumps(calib_json, ensure_ascii=False))
        auditoria.agregar("FASE 1 - AFU (PARALELO)", TEXTS[LANG]["sys_prompt_fase1"], json.dumps(afu_json, ensure_ascii=False))
        auditoria.agregar(
            "FASE 2 - ABAP (PARALELO)",
            f"{TEXTS[LANG]['sys_prompt_fase2']}\n\n[Pre-filtrado: {len(logica_objs)} objeto(s) con lógica enviados a IA de "
            f"{len(metadata_json.get('objects', []))} totales — {len(diccionario_objs)} de diccionario y "
            f"{len(ruido_objs)} de ruido técnico se documentaron sin IA]"
            + (f"\n[ATENCIÓN: {len(objetos_sin_analisis)} objeto(s) sin análisis de IA por JSON inválido tras reintentos: "
               f"{', '.join(objetos_sin_analisis)}]" if objetos_sin_analisis else ""),
            analisis_abap
        )

        resultado.afu_json = afu_json
        tono = calib_json.get('tone', 'Tono corporativo técnico')

        # ---------------------------------------------------------
        # ARMADO DEL DOCUMENTO: diccionario y ruido van directo desde
        # metadata (sin IA); solo los objetos con lógica pasan a Fase 4.
        # ---------------------------------------------------------
        # La IA en Fase 2 a veces "limpia" espacios al repetir el object_name
        # en su respuesta (ej. "ZCLPM_COWK=> M_GET_CONSTANTS" -> "ZCLPM_COWK=>M_GET_CONSTANTS").
        # Se normaliza espacios en ambos lados del cruce para no perder el
        # código real por una diferencia cosmética de formato.
        def _normalizar_nombre(n):
            """Colapsa los espacios repetidos de un nombre de objeto."""
            return " ".join((n or "").split())

        logica_meta_by_name = {_normalizar_nombre(o.get("object_name", "")): o for o in logica_objs}
        abap_dict = entrada.abap_dict

        # Los objetos TABU con 'customizing' (columns/rows ya estructurados
        # por el extractor) tienen su propia sección 5.3 con tabla dinámica;
        # el resto de diccionario sigue en 5.2 como antes.
        objetos_config = [o for o in diccionario_objs if o.get("customizing", {}).get("rows")]
        objetos_diccionario_resto = [o for o in diccionario_objs if not o.get("customizing", {}).get("rows")]

        # Tipos que NO tienen campos DDIC propios (procesan lógica, no
        # declaran estructura de datos propia) — si la IA de todos modos
        # "inventa" algo para 'ddic_fields' (ej. la estructura de una tabla
        # que el método solo LEE), se descarta para no mostrar información
        # ajena al objeto como si fuera suya.
        TIPOS_SIN_CAMPOS_PROPIOS = {"PROG", "CLAS", "CLAS_SECTION", "FUNC", "ENHO", "METH", "REPS"}

        dtss_final = {
            "objetos_logica": [
                {
                    "nombre": obj.get("object_name", ""),
                    "tipo": obj.get("object_type", ""),
                    "justificacion": "",
                    "logica_tecnica": "",
                    "campos": (
                        [] if obj.get("object_type", "") in TIPOS_SIN_CAMPOS_PROPIOS
                        else obj.get("technical_mechanics", {}).get("ddic_fields", [])
                    ),
                    "codigo": obtener_codigo_objeto(
                        logica_meta_by_name.get(_normalizar_nombre(obj.get("object_name", ""))), abap_dict),
                }
                for obj in analisis_abap_consolidado
            ],
            "objetos_diccionario": [objeto_diccionario_desde_metadata(o) for o in objetos_diccionario_resto],
            "configuracion": [
                {
                    "nombre": o.get("object_name", ""),
                    "descripcion": objeto_diccionario_desde_metadata(o).get("descripcion_tecnica", ""),
                    "columnas": o.get("customizing", {}).get("columns", []),
                    "filas": o.get("customizing", {}).get("rows", []),
                }
                for o in objetos_config
            ],
            "objetos_omitidos_ruido": [objeto_ruido_desde_metadata(o) for o in ruido_objs],
            "ordenes_transporte": []
        }

        # =========================================================
        # FASE 4: REDACCIÓN CORPORATIVA (usa el AFU como contexto
        # funcional general, sin mapear objeto por requisito)
        # =========================================================
        async with progreso(TEXTS[LANG]["zip_fase4"]):
            payload_para_ia = [
                {
                    "object_name": obj.get("object_name", ""),
                    "descripcion_cruda": obj.get("technical_mechanics", {}).get("description", "")
                }
                for obj in analisis_abap_consolidado
            ]

            reqs_activos = [r for r in afu_json.get("requirements", []) if r.get("status") == "activo"]
            resumen_reqs = "\n".join(
                f"- {r.get('title', '')}: {r.get('description', '')}" for r in reqs_activos
            ) or "No se identificaron requerimientos funcionales activos."
            contexto_funcional = (
                f"Objetivo general del proyecto: {afu_json.get('general_objective', 'No especificado.')}\n\n"
                f"Requerimientos de negocio activos:\n{resumen_reqs}"
            )

            sys_fase4 = TEXTS[LANG]["sys_prompt_fase4"] + f"\n\nTono requerido: {tono}"
            user_fase4 = TEXTS[LANG]["user_prompt_fase4"].format(
                contexto_funcional=contexto_funcional,
                json_objetos=json.dumps(payload_para_ia, ensure_ascii=False),
            )
            response_fase4 = await cliente_ia.completar(
                modelos.fase4, sys_fase4, user_fase4, temperature=0.0, max_tokens=8000,
            )

        json_ia_crudo = response_fase4.contenido
        json_ia_limpio = clean_json_response(json_ia_crudo)
        try:
            textos_ia = json.loads(json_ia_limpio)
        except json.JSONDecodeError:
            logger.error("Error crítico: La IA no devolvió un JSON válido en la Fase 4.")
            textos_ia = {"redacciones": []}

        auditoria.agregar("FASE 4 - TEXTOS GENERADOS", sys_fase4, json.dumps(textos_ia, indent=2, ensure_ascii=False))

        dict_textos = {t.get("nombre"): t for t in textos_ia.get("redacciones", [])}
        for obj in dtss_final["objetos_logica"]:
            if obj["nombre"] in dict_textos:
                obj["justificacion"] = dict_textos[obj["nombre"]].get("justificacion", "")
                obj["logica_tecnica"] = dict_textos[obj["nombre"]].get("logica_tecnica", "")

        resultado.borrador_json = json.dumps(dtss_final, indent=2, ensure_ascii=False)

        # ---------------------------------------------------------
        # GENERACIÓN DEL DOCUMENTO WORD FINAL (docxtpl)
        # ---------------------------------------------------------
        incluir_funcional = entrada.incluir_funcional
        tr_info = metadata_json.get('transport_request', {})
        contexto_docx = generador.construir_contexto(
            dtss_final,
            metadata_json,
            cliente=tr_info.get('author', 'Cliente'),
            proyecto=tr_info.get('description', 'Documentación Técnica'),
            # docxtpl necesita el texto escapado; el modo AFU escribe con python-docx directo
            escapar_xml=not incluir_funcional,
        )
        # Se loguea SIEMPRE (éxito o falla) el contexto que se le mandó a la
        # plantilla, para poder reproducir el armado del docx sin adivinar.
        auditoria.agregar(
            "FASE 5 - ARMADO DOCX (CONTEXTO ENVIADO A LA PLANTILLA)",
            ("base=AFU original + secciones técnicas (requisitos funcionales incluidos)" if incluir_funcional
             else f"plantilla={generador.plantilla_path}"),
            json.dumps(contexto_docx, indent=2, ensure_ascii=False)[:8000]
        )
        try:
            docx_path = os.path.join(dir_salida, f"DTSS_{get_file_timestamp()}.docx")
            if incluir_funcional:
                # DTSS = AFU original (secciones 1-4 tal cual) + secciones 5, 6 y 7 generadas
                afu_base_path = os.path.join(dir_salida, f"AFU_base_{get_file_timestamp()}.docx")
                try:
                    with open(afu_base_path, 'wb') as f_afu:
                        f_afu.write(entrada.afu_docx_bytes)
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, functools.partial(
                        generador.renderizar_con_afu, contexto_docx, afu_base_path, docx_path, dir_salida
                    ))
                finally:
                    if os.path.exists(afu_base_path):
                        os.remove(afu_base_path)
            else:
                generador.renderizar(contexto_docx, docx_path, dir_salida)
            resultado.docx_path = docx_path

            # CSV crudo (sin compactar columnas) por cada objeto de customizing,
            # para que el usuario pueda validar o recargar la configuración
            # exacta que se usó — la imagen del Word es para lectura humana,
            # el CSV es la fuente de verdad reprocesable.
            resultado.csvs_configuracion = [
                generador.exportar_csv(cfg, dir_salida)
                for cfg in dtss_final.get("configuracion", [])
            ]
            resultado.mensaje = (
                TEXTS[LANG]["msg_full_dtss_ok"] if incluir_funcional
                else "🎉 <b>Documento Técnico generado.</b>\n\nAdjunto el Word ya armado con la plantilla."
            )
        except DocxBuildError as docx_e:
            logger.error(f"Error generando el .docx final: {docx_e}")
            auditoria.agregar("FASE 5 - ARMADO DOCX (ERROR)", "render(contexto)", docx_e.trace_text)
            resultado.mensaje = (
                "⚠️ <b>El JSON se generó correctamente, pero falló el armado del Word.</b>\n"
                "Revisa el log de auditoría adjunto (sección 'FASE 5 - ARMADO DOCX') para el detalle "
                "completo: traceback, variables esperadas por la plantilla y el contexto usado.\n\n"
                "Adjunto el JSON para revisión manual."
            )
        except Exception as docx_e:
            # Cualquier otra falla inesperada (ruta de plantilla inválida, permisos, etc.)
            logger.error(f"Error inesperado generando el .docx final: {docx_e}")
            auditoria.agregar("FASE 5 - ARMADO DOCX (ERROR INESPERADO)", "render(contexto)", traceback.format_exc())
            resultado.mensaje = (
                "⚠️ <b>El JSON se generó correctamente, pero falló el armado del Word.</b>\n"
                f"Detalle: {docx_e}\n\nRevisa el log de auditoría adjunto para el traceback completo.\n\n"
                "Adjunto el JSON para revisión manual."
            )

    except Exception as e:
        logger.error(f"Error Pipeline: {e}")
        # Sin esto, una falla temprana (p. ej. la IA rechaza la llamada) dejaba
        # el registro de auditoría vacío y sin pista de lo ocurrido.
        auditoria.agregar("ERROR - PIPELINE INTERRUMPIDO", "(sin prompt: la ejecución no terminó)",
                          f"{type(e).__name__}: {e}")
        resultado.mensaje =TEXTS[LANG].get("zip_error", "Error interno") + f"\nDetalle: {e}"
        resultado.mensaje_html = False

    return resultado
