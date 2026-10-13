# pipeline.py
#
# Núcleo de la aplicación: lee la OT, orquesta las fases de IA y arma el
# documento. NO conoce el canal por el que llegan los archivos (Telegram hoy,
# otro mañana): recibe una EntradaPipeline y devuelve un ResultadoPipeline.
# El canal (bot.py) se encarga de descargar, mostrar progreso y enviar archivos.

import os
import json
import zipfile
import logging
import tempfile
import asyncio
import re
import traceback
import functools
import difflib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from document_parser import analyze_functional_text, analyze_calibration_text, clean_json_response
from messages import TEXTS
from sap_technical_tables import is_technical_noise
from document_builder import construir_contexto, renderizar_documento, DocxBuildError, exportar_csv_configuracion
from document_builder_afu import renderizar_documento_con_afu

LANG = "es"
logger = logging.getLogger(__name__)

# El modelo por defecto (nemotron-3-super-120b-a12b) razona por defecto
# (enable_thinking=True). Para las fases de extracción/mapeo JSON no queremos
# que el "pensar en voz alta" se coma el presupuesto de max_tokens ni compita
# con el truco de forzar el inicio de la respuesta.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}

# Solo estos tipos tienen lógica propia (escrita por un desarrollador) y
# ameritan pasar por Fase 2 (análisis IA) y Fase 4 (redacción IA).
# TABL/DTEL/DOMA/VIEW/SHLP/TRAN/TABU (customizing real) se documentan tal
# cual desde metadata.json, sin gastar tokens de IA en ellos.
# PROG/CLAS se mantienen por compatibilidad con OTs extraídas con el
# esquema viejo; REPS (includes de programa) y CLAS_SECTION (secciones
# PUBLIC/PROTECTED/PRIVATE de una clase) son los tipos del esquema nuevo
# del extractor, donde una clase ya no es un solo objeto CLAS sino que se
# descompone en sus secciones + un objeto METH por cada método.
LOGIC_TYPES = ["PROG", "CLAS", "CLAS_SECTION", "FUNC", "ENHO", "METH", "REPS"]


# ==========================================
# CONTRATO ENTRE EL CANAL Y EL NÚCLEO
# ==========================================
@dataclass(frozen=True)
class Modelos:
    """Modelo de IA a usar en cada fase que orquesta el núcleo."""
    fase1_5: str
    fase2: str
    fase4: str


@dataclass
class EntradaPipeline:
    metadata_json: dict
    abap_dict: dict
    calib_raw_text: str | None = None
    afu_raw_text: str | None = None
    afu_docx_bytes: bytes | None = None
    incluir_funcional: bool = False


@dataclass
class ResultadoPipeline:
    mensaje: str = ""
    mensaje_html: bool = True
    docx_path: str | None = None
    csvs_configuracion: list = field(default_factory=list)
    borrador_json: str | None = None
    afu_json: dict = field(default_factory=dict)


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


class ZipSinMetadataError(Exception):
    """El ZIP de la OT no trae metadata.json en la raíz."""


@asynccontextmanager
async def _sin_progreso(texto_base):
    yield


# ==========================================
# UTILIDADES Y HELPER FUNCTIONS
# ==========================================
def get_current_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_file_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def leer_zip_ot(zip_path):
    """Devuelve (metadata_json, abap_dict) de un ZIP de OT. abap_dict va
    indexado por nombre de archivo sin carpeta."""
    abap_files_dict = {}
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        if 'metadata.json' not in zip_ref.namelist():
            raise ZipSinMetadataError()
        with zip_ref.open('metadata.json') as meta_file:
            metadata_content = json.load(meta_file)

        for file_name in zip_ref.namelist():
            if file_name.endswith('.abap'):
                with zip_ref.open(file_name) as abap_file:
                    try:
                        content = abap_file.read().decode('utf-8')
                    except UnicodeDecodeError:
                        content = abap_file.read().decode('latin-1')
                    abap_files_dict[os.path.basename(file_name)] = content

    return metadata_content, abap_files_dict

def obtener_codigo_objeto(obj_meta, abap_dict):
    """Devuelve el código a mostrar para un objeto con lógica:
    - Objeto NUEVO (sin 'previous'): el código completo tal cual.
    - Sin cambios reales (previous == active): un aviso breve.
    - Objeto MODIFICADO: DOS secciones claramente separadas y etiquetadas
      — 'CÓDIGO NUEVO' primero (líneas agregadas/modificadas), 'CÓDIGO
      ANTERIOR' después (líneas eliminadas/reemplazadas) — nunca mezcladas
      en un solo bloque de diff con +/- intercalados, para evitar
      confusión visual sobre qué es lo vigente y qué ya no aplica.
    Nunca se le pide a la IA que retipee el código — se toma directo del
    fuente real para evitar errores de transcripción y ahorrar tokens."""
    files = (obj_meta or {}).get("files", {}) or {}
    active_name = files.get("active")
    previous_name = files.get("previous")
    activo = abap_dict.get(active_name, "") if active_name else ""
    previo = abap_dict.get(previous_name, "") if previous_name else ""

    if not activo:
        return {"nuevo": "N/A", "anterior": "N/A"}
    if not previo:
        return {"nuevo": activo, "anterior": "N/A"}
    if previo == activo:
        return {"nuevo": "Sin cambios de código respecto a la versión anterior.", "anterior": "N/A"}

    diff_lines = list(difflib.unified_diff(previo.splitlines(), activo.splitlines(), lineterm=""))
    lineas_nuevas = [l[1:] for l in diff_lines if l.startswith("+") and not l.startswith("+++")]
    lineas_anteriores = [l[1:] for l in diff_lines if l.startswith("-") and not l.startswith("---")]

    nuevo_str = "\n".join(lineas_nuevas) if lineas_nuevas else "Sin adiciones."
    anterior_str = "\n".join(lineas_anteriores) if lineas_anteriores else "Sin eliminaciones."
    return {"nuevo": nuevo_str, "anterior": anterior_str}

def clasificar_objetos_previos(metadata_json):
    """
    Pre-filtra los objetos ANTES de gastar tokens de IA (Fase 2), en vez de
    mandar todo el ZIP a la IA y descartar después:
      - ruido: objetos técnicos autogenerados por SAP (TABU tipo TDDAT/TVDIR,
        FUNC/PROG tipo VIEWFRAME_*/SAPL*, o un FUGR contenedor sin código
        propio). Se documentan directo desde metadata, sin IA.
      - diccionario: objetos simples de SAP (tablas, elementos de datos,
        dominios, vistas, ayudas de búsqueda, transacciones, customizing
        real como ZTCONSTANTES). Se documentan tal cual desde metadata, sin IA.
      - logica: objetos con lógica propia (PROG/CLAS/FUNC/ENHO/METH). Solo
        estos se mandan a Fase 2 para análisis técnico con IA.
    """
    ruido, diccionario, logica = [], [], []
    for obj in metadata_json.get("objects", []):
        obj_type = obj.get("object_type", "")
        obj_name = obj.get("object_name", "")
        tiene_codigo = bool(obj.get("files", {}).get("active"))

        if is_technical_noise(obj_type, obj_name) or (obj_type == "FUGR" and not tiene_codigo):
            ruido.append(obj)
        elif obj_type in LOGIC_TYPES:
            logica.append(obj)
        else:
            diccionario.append(obj)

    return ruido, diccionario, logica

def objeto_diccionario_desde_metadata(obj):
    """Arma la entrada de 'objetos_diccionario' directo desde metadata.json,
    sin ninguna llamada a IA."""
    attrs = obj.get("attributes", []) or []
    attrs_dict = {a.get("key"): a.get("value") for a in attrs if a.get("key")}
    # "Descripción" (DTEL/TABL/VIEW) o "Texto Breve" (TRAN) son mucho más
    # útiles que el short_text genérico que a veces manda el extractor
    # (ej. "Objeto del Dictionary (DDIC)", "Código de Transacción").
    descripcion = (
        attrs_dict.get("Descripción")
        or attrs_dict.get("Texto Breve")
        or obj.get("short_text")
        or "Objeto de diccionario SAP."
    )

    customizing = obj.get("customizing")
    if customizing and customizing.get("rows"):
        columnas = customizing.get("columns", [])
        campos = [dict(zip(columnas, fila)) for fila in customizing.get("rows", [])]
    else:
        campos = obj.get("fields") or attrs

    return {
        "nombre": obj.get("object_name", ""),
        "tipo": obj.get("object_type", ""),
        "descripcion_tecnica": descripcion,
        "campos": campos,
    }

def objeto_ruido_desde_metadata(obj):
    return {
        "nombre": obj.get("object_name", ""),
        "tipo": obj.get("object_type", ""),
        "motivo": "Generado automáticamente por SAP, sin lógica propia."
    }

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


# ==========================================
# CODE REVIEW (OPCIONAL)
# ==========================================
async def revisar_codigo(cliente_ia, modelo, metadata_str, abap_code_str, auditoria: RegistroAuditoria):
    """Fase 1.5: devuelve el dict del Code Review ({'has_violations': ..., 'findings': [...]})."""
    sys_prompt = TEXTS[LANG]["sys_prompt_fase1_5"]
    usr_prompt = TEXTS[LANG]["user_prompt_fase1_5"].format(metadata_json=metadata_str, codigo_abap=abap_code_str)

    response_fase1_5 = await cliente_ia.chat.completions.create(
        model=modelo,
        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": usr_prompt}],
        temperature=0.1, max_tokens=1500,
        extra_body=NO_THINKING,
    )

    review_txt = clean_json_response(response_fase1_5.choices[0].message.content.strip())
    auditoria.agregar("FASE 1.5 - CODE REVIEW", sys_prompt, review_txt)

    try:
        return json.loads(review_txt.strip())
    except json.JSONDecodeError:
        logger.warning("Fallo al parsear JSON del Code Review. Ignorando validaciones.")
        return {"has_violations": False}


# ==========================================
# FUNCIONES ASÍNCRONAS DEL PIPELINE IA
# ==========================================
async def task_phase_0(raw_text, cliente_ia):
    if not raw_text: return {}
    return await analyze_calibration_text(raw_text, cliente_ia)

async def task_phase_1(raw_text, cliente_ia):
    if not raw_text: return {}
    return await analyze_functional_text(raw_text, cliente_ia)

async def task_phase_2(meta_str, abap_str, semaforo, delay_offset, cliente_ia, modelo):
    await asyncio.sleep(delay_offset)
    async with semaforo:
        max_retries = 3
        base_delay = 2
        for attempt in range(max_retries):
            try:
                response = await cliente_ia.chat.completions.create(
                    model=modelo,
                    messages=[
                        {"role": "system", "content": TEXTS[LANG]["sys_prompt_fase2"]},
                        {"role": "user", "content": TEXTS[LANG]["user_prompt_fase2"].format(metadata_json=meta_str, codigo_abap=abap_str)}
                    ],
                    temperature=0.2,
                    max_tokens=3500,
                    extra_body=NO_THINKING,
                )
                contenido = response.choices[0].message.content
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

async def ejecutar_pipeline(entrada: EntradaPipeline, cliente_ia, modelos: Modelos,
                            auditoria: RegistroAuditoria, progreso=_sin_progreso,
                            dir_salida: str = None) -> ResultadoPipeline:
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
                asyncio.create_task(task_phase_0(entrada.calib_raw_text, cliente_ia)),
                asyncio.create_task(task_phase_1(entrada.afu_raw_text, cliente_ia))
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
            calib_json = TEXTS[LANG]["default_calibration"]
            log_prompt_fase0 = "FASE 0 OMITIDA: Se aplicó el perfil corporativo por defecto del sistema."

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
            "constantes": [],
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
            response_fase4 = await cliente_ia.chat.completions.create(
                model=modelos.fase4,
                messages=[
                    {"role": "system", "content": sys_fase4},
                    {"role": "user", "content": user_fase4},
                ], temperature=0.0, max_tokens=8000,
                extra_body=NO_THINKING,
            )

        # Con "enable_thinking": False el modelo genera el objeto completo por su
        # cuenta; no se concatena ningún prefijo manual.
        json_ia_crudo = response_fase4.choices[0].message.content
        json_ia_limpio = clean_json_response(json_ia_crudo)
        try:
            textos_ia = json.loads(json_ia_limpio)
        except json.JSONDecodeError:
            logger.error("Error crítico: La IA no devolvió un JSON válido en la Fase 4.")
            textos_ia = {"redacciones": [], "constantes": []}

        auditoria.agregar("FASE 4 - TEXTOS GENERADOS", sys_fase4, json.dumps(textos_ia, indent=2, ensure_ascii=False))

        dict_textos = {t.get("nombre"): t for t in textos_ia.get("redacciones", [])}
        for obj in dtss_final["objetos_logica"]:
            if obj["nombre"] in dict_textos:
                obj["justificacion"] = dict_textos[obj["nombre"]].get("justificacion", "")
                obj["logica_tecnica"] = dict_textos[obj["nombre"]].get("logica_tecnica", "")

        dtss_final["constantes"] = textos_ia.get("constantes", [])
        resultado.borrador_json = json.dumps(dtss_final, indent=2, ensure_ascii=False)

        # ---------------------------------------------------------
        # GENERACIÓN DEL DOCUMENTO WORD FINAL (docxtpl)
        # ---------------------------------------------------------
        incluir_funcional = entrada.incluir_funcional
        tr_info = metadata_json.get('transport_request', {})
        contexto_docx = construir_contexto(
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
             else f"plantilla={os.getenv('PLANTILLA_DTSS_PATH', 'plantilla_dtss.docx')}"),
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
                        renderizar_documento_con_afu, contexto_docx, afu_base_path, docx_path, dir_salida
                    ))
                finally:
                    if os.path.exists(afu_base_path):
                        os.remove(afu_base_path)
            else:
                renderizar_documento(contexto_docx, docx_path, imagenes_tmp_dir=dir_salida)
            resultado.docx_path = docx_path

            # CSV crudo (sin compactar columnas) por cada objeto de customizing,
            # para que el usuario pueda validar o recargar la configuración
            # exacta que se usó — la imagen del Word es para lectura humana,
            # el CSV es la fuente de verdad reprocesable.
            resultado.csvs_configuracion = [
                exportar_csv_configuracion(cfg, dir_salida)
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
        resultado.mensaje = TEXTS[LANG].get("zip_error", "Error interno") + f"\nDetalle: {e}"
        resultado.mensaje_html = False

    return resultado
