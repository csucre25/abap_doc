import os
import json
import zipfile
import logging
import tempfile
import html
import asyncio  
import random
import re
import traceback
import functools
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
from openai import AsyncOpenAI
from document_parser import extract_text_from_file, analyze_functional_text, analyze_calibration_text, clean_json_response
from messages import TEXTS
from sap_technical_tables import is_technical_noise
from document_builder import construir_contexto, renderizar_documento, DocxBuildError, exportar_csv_configuracion
from document_builder_afu import renderizar_documento_con_afu

# ==========================================
# CONFIGURACIÓN INICIAL Y LOGGING
# ==========================================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
LANG = "es"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
# httpx (usado por python-telegram-bot) loguea cada request en INFO con la URL completa,
# y esa URL incluye el token del bot. Se sube a WARNING para que no quede en consola/logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

nvidia_client = AsyncOpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY) 

# El modelo por defecto (nemotron-3-super-120b-a12b) razona por defecto
# (enable_thinking=True). Para las fases de extracción/mapeo JSON no queremos
# que el "pensar en voz alta" se coma el presupuesto de max_tokens ni compita
# con el truco de forzar el inicio de la respuesta.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}

# CARGA DE MODELOS CON FALLBACK AL DEFAULT
MODEL_DEFAULT   = os.getenv("LLM_MODEL_DEFAULT", "nvidia/nemotron-3-super-120b-a12b")
MODEL_FASE1_5   = os.getenv("LLM_MODEL_FASE1_5", MODEL_DEFAULT)
MODEL_FASE2     = os.getenv("LLM_MODEL_FASE2", MODEL_DEFAULT)
MODEL_FASE4     = os.getenv("LLM_MODEL_FASE4", MODEL_DEFAULT)

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
# ESTADOS DE CONVERSACIÓN
# ==========================================
WAITING_CALIBRATION, WAITING_AFU, WAITING_ZIP, WAITING_CR_CHOICE, WAITING_CR_DECISION = range(5)

# ==========================================
# UTILIDADES Y HELPER FUNCTIONS
# ==========================================
def get_current_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_file_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def append_to_log(context: ContextTypes.DEFAULT_TYPE, phase: str, prompt: str, response: str):
    timestamp = get_current_timestamp()
    log_entry = (
        f"\n\n{'='*50}\n[{phase}] - {timestamp}\n{'='*50}\n"
        f"--- PROMPT ENVIADO ---\n{prompt}\n\n"
        f"--- RESPUESTA DEL LLM ---\n{response}\n"
    )
    context.user_data['audit_log'] = context.user_data.get('audit_log', '') + log_entry

async def loading_animation(status_msg, base_text, phase_key="loading_phrases_tech"):
    phrases = TEXTS[LANG].get(phase_key, TEXTS[LANG]["loading_phrases_tech"]).copy()
    random.shuffle(phrases)
    try:
        idx = 0
        while True:
            phrase = phrases[idx % len(phrases)]
            dots = "." * ((idx % 3) + 1)
            try:
                await status_msg.edit_text(f"{base_text}\n\n⏳ <i>{phrase}{dots}</i>", parse_mode='HTML')
            except Exception:
                pass  
            idx += 1
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass  

import difflib

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
    a la IA, pero permite que execute_pipeline ignore lo que la IA diga en
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

# ==========================================
# HANDLERS: FLUJO DE DOCUMENTOS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [[InlineKeyboardButton(TEXTS[LANG]["btn_skip_calib"], callback_data='skip_calib')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(TEXTS[LANG]["start_v2"], reply_markup=reply_markup)
    return WAITING_CALIBRATION

async def skip_calibration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['calib_raw_text'] = None
    await query.edit_message_text(TEXTS[LANG]["msg_calib_skipped"], parse_mode='HTML')
    return WAITING_AFU

async def handle_calibration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document.file_name.endswith(('.docx', '.pdf')):
        await update.message.reply_text(TEXTS[LANG]["msg_invalid_word"])
        return WAITING_CALIBRATION

    status_msg = await update.message.reply_text(TEXTS[LANG]["msg_calib_analyzing"])
    try:
        file = await context.bot.get_file(document.file_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, document.file_name)
            await file.download_to_drive(file_path)
            context.user_data['calib_raw_text'] = extract_text_from_file(file_path)
            
        await status_msg.edit_text(TEXTS[LANG]["msg_calib_success"], parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error en Calibración: {e}")
        await status_msg.edit_text(TEXTS[LANG]["afu_error"])
    return WAITING_AFU

async def handle_afu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document.file_name.endswith(('.docx', '.pdf')):
        await update.message.reply_text(TEXTS[LANG]["msg_invalid_word"])
        return WAITING_AFU

    status_msg = await update.message.reply_text(TEXTS[LANG]["msg_afu_analyzing"])
    try:
        file = await context.bot.get_file(document.file_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, document.file_name)
            await file.download_to_drive(file_path)
            context.user_data['afu_raw_text'] = extract_text_from_file(file_path)
            # Para "técnica + requisitos funcionales" el AFU es el documento base,
            # así que se conserva el archivo original (solo .docx; un PDF no se puede extender).
            context.user_data['afu_docx_bytes'] = None
            if document.file_name.lower().endswith('.docx'):
                with open(file_path, 'rb') as f_afu:
                    context.user_data['afu_docx_bytes'] = f_afu.read()
            
        await status_msg.edit_text(TEXTS[LANG]["msg_afu_success_v2"].format("(Pendiente de procesamiento paralelo)"), parse_mode='HTML')
    except Exception as e:
        logger.error(TEXTS[LANG]["log_error_afu"].format(e))
        await status_msg.edit_text(TEXTS[LANG]["afu_error"])
    return WAITING_ZIP

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document.file_name.endswith('.zip'):
        await update.message.reply_text(TEXTS[LANG]["msg_invalid_zip"])
        return WAITING_ZIP

    status_msg = await update.message.reply_text(TEXTS[LANG]["msg_zip_downloading_v2"])
    try:
        file = await context.bot.get_file(document.file_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, document.file_name)
            await file.download_to_drive(zip_path)
            
            metadata_content = {}
            abap_files_dict = {}
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                if 'metadata.json' in zip_ref.namelist():
                    with zip_ref.open('metadata.json') as meta_file:
                        metadata_content = json.load(meta_file)
                else:
                    await status_msg.edit_text(TEXTS[LANG]["zip_missing_meta"])
                    return WAITING_ZIP
                
                for file_name in zip_ref.namelist():
                    if file_name.endswith('.abap'):
                        with zip_ref.open(file_name) as abap_file:
                            try:
                                content = abap_file.read().decode('utf-8')
                            except UnicodeDecodeError:
                                content = abap_file.read().decode('latin-1')
                            #abap_files_dict[file_name] = content
                            abap_files_dict[os.path.basename(file_name)] = content
            
            context.user_data['metadata_json'] = metadata_content
            context.user_data['abap_dict'] = abap_files_dict
            context.user_data['metadata_str'] = json.dumps(metadata_content, indent=2)
            context.user_data['abap_code_str'] = "\n".join([f"--- ARCHIVO: {k} ---\n{v}\n\n" for k,v in abap_files_dict.items()])

            keyboard = [
                [InlineKeyboardButton(TEXTS[LANG]["btn_run_cr"], callback_data='run_cr')],
                [InlineKeyboardButton(TEXTS[LANG]["btn_skip_cr"], callback_data='skip_cr')]
            ]
            # "Técnica + requisitos funcionales" solo si el AFU llegó como .docx
            if context.user_data.get('afu_docx_bytes'):
                keyboard.append(
                    [InlineKeyboardButton(TEXTS[LANG]["btn_skip_cr_full"], callback_data='skip_cr_full')]
                )
            await status_msg.edit_text(TEXTS[LANG]["msg_zip_ask_cr"], reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['internal_status_msg'] = status_msg
            
            return WAITING_CR_CHOICE
            
    except Exception as e:
        logger.error(TEXTS[LANG]["log_error_zip"].format(e))
        await status_msg.edit_text(TEXTS[LANG]["zip_error"])
        return WAITING_ZIP

# ==========================================
# HANDLERS: CODE REVIEW (OPCIONAL)
# ==========================================
async def handle_cr_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    status_msg = context.user_data['internal_status_msg']

    # Solo el botón "técnica + requisitos funcionales" activa el modo nuevo;
    # el resto de caminos (incluido el Code Review) se comporta como siempre.
    context.user_data['incluir_funcional'] = (query.data == 'skip_cr_full')

    if query.data in ('skip_cr', 'skip_cr_full'):
        await query.edit_message_text(TEXTS[LANG]["msg_cr_skipped"])
        await execute_pipeline(update, context)
        return ConversationHandler.END

    await query.edit_message_text(TEXTS[LANG]["msg_cr_analyzing"])
    sys_prompt = TEXTS[LANG]["sys_prompt_fase1_5"]
    usr_prompt = TEXTS[LANG]["user_prompt_fase1_5"].format(metadata_json=context.user_data['metadata_str'], codigo_abap=context.user_data['abap_code_str'])
    
    loader_task = asyncio.create_task(loading_animation(status_msg, TEXTS[LANG]["msg_cr_analyzing"]))
    try:
        response_fase1_5 = await nvidia_client.chat.completions.create(
            model=MODEL_FASE1_5, 
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": usr_prompt}],
            temperature=0.1, max_tokens=1500,
            extra_body=NO_THINKING,
        )
    finally:
        loader_task.cancel()
    
    review_txt = clean_json_response(response_fase1_5.choices[0].message.content.strip())
    append_to_log(context, "FASE 1.5 - CODE REVIEW", sys_prompt, review_txt)

    try:
        code_review = json.loads(review_txt.strip())
    except json.JSONDecodeError:
        logger.warning("Fallo al parsear JSON del Code Review. Ignorando validaciones.")
        code_review = {"has_violations": False}

    if code_review.get("has_violations", False):
        findings_list = "\n".join([f"• {html.escape(f.get('description', ''))}" for f in code_review.get("findings", [])])
        keyboard = [
            [InlineKeyboardButton(TEXTS[LANG]["btn_ignore_cr"], callback_data='ignore_cr')],
            [InlineKeyboardButton(TEXTS[LANG]["btn_cancel_cr"], callback_data='cancel_cr')]
        ]
        await status_msg.edit_text(
            TEXTS[LANG]["msg_cr_violations"].format(findings_list), 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode='HTML'
        )
        return WAITING_CR_DECISION
    else:
        await status_msg.edit_text(TEXTS[LANG]["msg_cr_clean"])
        await execute_pipeline(update, context)
        return ConversationHandler.END

async def handle_cr_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'ignore_cr':
        await query.edit_message_text(TEXTS[LANG]["msg_cr_skipped"]) 
        await execute_pipeline(update, context)
        return ConversationHandler.END
    else:
        await query.edit_message_text(TEXTS[LANG]["msg_process_cancelled"])
        return WAITING_ZIP 

# ==========================================
# FUNCIONES ASÍNCRONAS DEL PIPELINE IA
# ==========================================
async def task_phase_0(raw_text):
    if not raw_text: return {}
    return await analyze_calibration_text(raw_text, nvidia_client)

async def task_phase_1(raw_text):
    if not raw_text: return {}
    return await analyze_functional_text(raw_text, nvidia_client)

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

async def task_phase_2(meta_str, abap_str, semaforo, delay_offset):
    await asyncio.sleep(delay_offset) 
    async with semaforo:
        max_retries = 3
        base_delay = 2
        for attempt in range(max_retries):
            try:
                response = await nvidia_client.chat.completions.create(
                    model=MODEL_FASE2,
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

async def execute_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = context.user_data['internal_status_msg']
    
    try:
        # FASE 0, 1 Y 2 (Paralelo)
        base_text_paralelo = TEXTS[LANG]["zip_fase_paralela"]
        loader_task = asyncio.create_task(loading_animation(status_msg, base_text_paralelo)) 
        try:
            metadata_json = context.user_data['metadata_json']
            ruido_objs, diccionario_objs, logica_objs = clasificar_objetos_previos(metadata_json)
            system_info = metadata_json.get('system_info', {})
            batches = create_processing_batches(logica_objs, system_info, context.user_data['abap_dict'])
            semaforo = asyncio.Semaphore(3) 
            tareas = [
                asyncio.create_task(task_phase_0(context.user_data.get('calib_raw_text'))),
                asyncio.create_task(task_phase_1(context.user_data.get('afu_raw_text')))
            ]
            batch_obj_unicos = []  # paralelo a las tareas de Fase 2, mismo orden
            for index, (meta_str, abap_str, obj_unico) in enumerate(batches):
                delay_offset = index * 0.5 
                tareas.append(asyncio.create_task(task_phase_2(meta_str, abap_str, semaforo, delay_offset)))
                batch_obj_unicos.append(obj_unico)
            
            resultados = await asyncio.gather(*tareas)
        finally:
            loader_task.cancel()
            
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
            
        append_to_log(context, "FASE 0 - CALIBRACIÓN", log_prompt_fase0, json.dumps(calib_json, ensure_ascii=False))
        append_to_log(context, "FASE 1 - AFU (PARALELO)", TEXTS[LANG]["sys_prompt_fase1"], json.dumps(afu_json, ensure_ascii=False))
        append_to_log(
            context, "FASE 2 - ABAP (PARALELO)",
            f"{TEXTS[LANG]['sys_prompt_fase2']}\n\n[Pre-filtrado: {len(logica_objs)} objeto(s) con lógica enviados a IA de "
            f"{len(metadata_json.get('objects', []))} totales — {len(diccionario_objs)} de diccionario y "
            f"{len(ruido_objs)} de ruido técnico se documentaron sin IA]"
            + (f"\n[ATENCIÓN: {len(objetos_sin_analisis)} objeto(s) sin análisis de IA por JSON inválido tras reintentos: "
               f"{', '.join(objetos_sin_analisis)}]" if objetos_sin_analisis else ""),
            analisis_abap
        )
        
        context.user_data['afu_json'] = afu_json
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
        abap_dict = context.user_data['abap_dict']

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
        #TIPOS_SIN_CAMPOS_PROPIOS = {"METH"}
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
        base_text_f4 = TEXTS[LANG]["zip_fase4"]
        loader_task = asyncio.create_task(loading_animation(status_msg, base_text_f4))
        try:
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
            response_fase4 = await nvidia_client.chat.completions.create(
                model=MODEL_FASE4,
                messages=[
                    {"role": "system", "content": sys_fase4}, 
                    {"role": "user", "content": user_fase4},
                ], temperature=0.0, max_tokens=8000,
                extra_body=NO_THINKING,
            )
        finally: 
            loader_task.cancel()
            
        # Con "enable_thinking": False el modelo genera el objeto completo por su
        # cuenta; no se concatena ningún prefijo manual.
        json_ia_crudo = response_fase4.choices[0].message.content
        json_ia_limpio = clean_json_response(json_ia_crudo)
        try:
            textos_ia = json.loads(json_ia_limpio)
        except json.JSONDecodeError:
            logger.error("Error crítico: La IA no devolvió un JSON válido en la Fase 4.")
            textos_ia = {"redacciones": [], "constantes": []}
            
        append_to_log(context, "FASE 4 - TEXTOS GENERADOS", sys_fase4, json.dumps(textos_ia, indent=2, ensure_ascii=False))

        dict_textos = {t.get("nombre"): t for t in textos_ia.get("redacciones", [])}
        for obj in dtss_final["objetos_logica"]:
            if obj["nombre"] in dict_textos:
                obj["justificacion"] = dict_textos[obj["nombre"]].get("justificacion", "")
                obj["logica_tecnica"] = dict_textos[obj["nombre"]].get("logica_tecnica", "")
                
        dtss_final["constantes"] = textos_ia.get("constantes", [])
        documento_final_json = json.dumps(dtss_final, indent=2, ensure_ascii=False)
        context.user_data['borrador_json'] = documento_final_json

        # ---------------------------------------------------------
        # GENERACIÓN DEL DOCUMENTO WORD FINAL (docxtpl)
        # ---------------------------------------------------------
        incluir_funcional = context.user_data.get('incluir_funcional', False)
        tr_info = context.user_data['metadata_json'].get('transport_request', {})
        contexto_docx = construir_contexto(
            dtss_final,
            context.user_data['metadata_json'],
            cliente=tr_info.get('author', 'Cliente'),
            proyecto=tr_info.get('description', 'Documentación Técnica'),
            # docxtpl necesita el texto escapado; el modo AFU escribe con python-docx directo
            escapar_xml=not incluir_funcional,
        )
        # Se loguea SIEMPRE (éxito o falla) el contexto que se le mandó a la
        # plantilla, para poder reproducir el armado del docx sin adivinar.
        append_to_log(
            context, "FASE 5 - ARMADO DOCX (CONTEXTO ENVIADO A LA PLANTILLA)",
            ("base=AFU original + secciones técnicas (requisitos funcionales incluidos)" if incluir_funcional
             else f"plantilla={os.getenv('PLANTILLA_DTSS_PATH', 'plantilla_dtss.docx')}"),
            json.dumps(contexto_docx, indent=2, ensure_ascii=False)[:8000]
        )
        try:
            docx_path = os.path.join(tempfile.gettempdir(), f"DTSS_{get_file_timestamp()}.docx")
            if incluir_funcional:
                # DTSS = AFU original (secciones 1-4 tal cual) + secciones 5, 6 y 7 generadas
                afu_base_path = os.path.join(tempfile.gettempdir(), f"AFU_base_{get_file_timestamp()}.docx")
                try:
                    with open(afu_base_path, 'wb') as f_afu:
                        f_afu.write(context.user_data['afu_docx_bytes'])
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, functools.partial(
                        renderizar_documento_con_afu, contexto_docx, afu_base_path, docx_path, tempfile.gettempdir()
                    ))
                finally:
                    if os.path.exists(afu_base_path):
                        os.remove(afu_base_path)
            else:
                renderizar_documento(contexto_docx, docx_path, imagenes_tmp_dir=tempfile.gettempdir())
            context.user_data['docx_final_path'] = docx_path

            # CSV crudo (sin compactar columnas) por cada objeto de customizing,
            # para que el usuario pueda validar o recargar la configuración
            # exacta que se usó — la imagen del Word es para lectura humana,
            # el CSV es la fuente de verdad reprocesable.
            context.user_data['csvs_configuracion'] = [
                exportar_csv_configuracion(cfg, tempfile.gettempdir())
                for cfg in dtss_final.get("configuracion", [])
            ]
            mensaje_exito = (
                TEXTS[LANG]["msg_full_dtss_ok"] if incluir_funcional
                else "🎉 <b>Documento Técnico generado.</b>\n\nAdjunto el Word ya armado con la plantilla."
            )
        except DocxBuildError as docx_e:
            logger.error(f"Error generando el .docx final: {docx_e}")
            append_to_log(context, "FASE 5 - ARMADO DOCX (ERROR)", "render(contexto)", docx_e.trace_text)
            mensaje_exito = (
                "⚠️ <b>El JSON se generó correctamente, pero falló el armado del Word.</b>\n"
                "Revisa el log de auditoría adjunto (sección 'FASE 5 - ARMADO DOCX') para el detalle "
                "completo: traceback, variables esperadas por la plantilla y el contexto usado.\n\n"
                "Adjunto el JSON para revisión manual."
            )
        except Exception as docx_e:
            # Cualquier otra falla inesperada (ruta de plantilla inválida, permisos, etc.)
            logger.error(f"Error inesperado generando el .docx final: {docx_e}")
            append_to_log(context, "FASE 5 - ARMADO DOCX (ERROR INESPERADO)", "render(contexto)", traceback.format_exc())
            mensaje_exito = (
                "⚠️ <b>El JSON se generó correctamente, pero falló el armado del Word.</b>\n"
                f"Detalle: {docx_e}\n\nRevisa el log de auditoría adjunto para el traceback completo.\n\n"
                "Adjunto el JSON para revisión manual."
            )
        await status_msg.edit_text(mensaje_exito, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error Pipeline: {e}")
        await status_msg.edit_text(TEXTS[LANG].get("zip_error", "Error interno") + f"\nDetalle: {e}") 
        
    finally:
        try:
            file_ts = get_file_timestamp()
            
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.txt', encoding='utf-8') as tmp_file:
                tmp_file.write(context.user_data.get('audit_log', 'Sin logs registrados.'))
                tmp_path = tmp_file.name
            await context.bot.send_document(
                chat_id=status_msg.chat_id, 
                document=open(tmp_path, 'rb'), 
                filename=f"Auditoria_Agentes_Log_{file_ts}.txt", 
                caption=TEXTS[LANG]["msg_log_caption"]
            )
            os.remove(tmp_path)
            
            if 'docx_final_path' in context.user_data and os.path.exists(context.user_data['docx_final_path']):
                es_completo = context.user_data.get('incluir_funcional', False)
                await context.bot.send_document(
                    chat_id=status_msg.chat_id,
                    document=open(context.user_data['docx_final_path'], 'rb'),
                    filename=f"DTSS_Completo_{file_ts}.docx" if es_completo else f"DTSS_{file_ts}.docx",
                    caption=TEXTS[LANG]["msg_full_dtss_caption"] if es_completo else TEXTS[LANG]["msg_draw_doc"]
                )
                os.remove(context.user_data['docx_final_path'])

            for ruta_csv in context.user_data.get('csvs_configuracion', []):
                if os.path.exists(ruta_csv):
                    await context.bot.send_document(
                        chat_id=status_msg.chat_id,
                        document=open(ruta_csv, 'rb'),
                        filename=os.path.basename(ruta_csv),
                        caption=TEXTS[LANG]["msg_csv_config"]
                    )
                    os.remove(ruta_csv)

            # El JSON se conserva siempre como respaldo/auditoría, incluso si el .docx sí se generó
            if 'borrador_json' in context.user_data:
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json', encoding='utf-8') as tmp_json:
                    tmp_json.write(context.user_data['borrador_json'])
                    tmp_json_path = tmp_json.name
                await context.bot.send_document(
                    chat_id=status_msg.chat_id, 
                    document=open(tmp_json_path, 'rb'), 
                    filename=f"DTSS_Estructurado_{file_ts}.json", 
                    caption=TEXTS[LANG]["msg_json_backup"]
                )
                os.remove(tmp_json_path)
                
        except Exception as log_e:
            logger.error(TEXTS[LANG]["msg_critical_error"].format(log_e=log_e))

# ==========================================
# GESTIÓN GLOBAL Y ARRANQUE
# ==========================================
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TEXTS[LANG]["msg_global_cancel"])
    context.user_data.clear()
    return ConversationHandler.END

async def handle_unexpected_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Atrapa cualquier texto o imagen enviada por error o fuera de flujo."""
    mensaje = "⚠️ <b>Entrada no reconocida.</b>\n\nEstoy esperando que subas un archivo (.docx, .pdf, o .zip) o presiones un botón.\n\nSi deseas iniciar un nuevo análisis, escribe /start"
    await update.message.reply_text(mensaje, parse_mode='HTML')

def main():
    if not TELEGRAM_TOKEN or not NVIDIA_API_KEY:
        logger.error(TEXTS[LANG]["log_missing_creds"])
        return
        
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(30.0) 
        .read_timeout(30.0)     
        .build()
    )
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            WAITING_CALIBRATION: [
                MessageHandler(filters.Document.ALL, handle_calibration), 
                CallbackQueryHandler(skip_calibration, pattern='^skip_calib$')
            ],
            WAITING_AFU: [MessageHandler(filters.Document.ALL, handle_afu)],
            WAITING_ZIP: [MessageHandler(filters.Document.ALL, handle_zip)],
            WAITING_CR_CHOICE: [CallbackQueryHandler(handle_cr_choice)],
            WAITING_CR_DECISION: [CallbackQueryHandler(handle_cr_decision)]
        },
        fallbacks=[
            CommandHandler('cancel', cancel_command),
            # ESTA ES LA LÍNEA MÁGICA QUE SOLUCIONA EL "ME IGNORA"
            MessageHandler(filters.ALL & ~filters.COMMAND, handle_unexpected_input)
        ]
    )
    
    app.add_handler(conv_handler)
    # MANEJADOR GLOBAL POR SI HABLAN FUERA DE UNA CONVERSACIÓN
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unexpected_input))
    
    logger.info(TEXTS[LANG]["log_bot_listening"])
    app.run_polling()

if __name__ == "__main__":
    main()