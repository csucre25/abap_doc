"""Canal Telegram: conversación paso a paso con el usuario.

Solo se ocupa del canal: control de acceso, descarga de archivos, botones,
animación de progreso, registro en la bitácora y envío de resultados. El
trabajo de documentar lo hace sapdoc.application.documentar_ot.

Conversación: /start → calibración (o saltar) → AFU → ZIP de la OT →
[Code Review → ignorar o cancelar] → generación y envío del Word."""

import os
import json
import logging
import tempfile
import html
import asyncio
import random
import time
import uuid
from dataclasses import asdict
from contextlib import asynccontextmanager
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application, ApplicationHandlerStop, CommandHandler, MessageHandler, CallbackQueryHandler,
    TypeHandler, filters, ContextTypes, ConversationHandler
)
from sapdoc.application.auditoria_ejecucion import RegistroAuditoria
from sapdoc.application.documentar_ot import ejecutar_pipeline
from sapdoc.application.marcas_tiempo import get_file_timestamp
from sapdoc.application.puertos import ClienteIA
from sapdoc.application.revision_codigo import revisar_codigo
from sapdoc.application.textos import TEXTS
from sapdoc.domain.entidades import EntradaPipeline
from sapdoc.infrastructure.bitacora import Bitacora, huella_archivo, sha256_archivo, sha256_bytes
from sapdoc.infrastructure.config import Config, ConfigError, cargar_config
from sapdoc.infrastructure.ia import crear_cliente_ia
from sapdoc.infrastructure.lectura_documentos import extract_text_from_file
from sapdoc.infrastructure.word import GeneradorWord
from sapdoc.infrastructure.zip_ot import ZipSinMetadataError, leer_zip_ot

# ==========================================
# LOGGING (la configuración se carga y valida en main(), ver config.py)
# ==========================================
LANG = "es"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
# httpx (usado por python-telegram-bot) loguea cada request en INFO con la URL completa,
# y esa URL incluye el token del bot. Se sube a WARNING para que no quede en consola/logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ==========================================
# ESTADOS DE CONVERSACIÓN
# ==========================================
WAITING_CALIBRATION, WAITING_AFU, WAITING_ZIP, WAITING_CR_CHOICE, WAITING_CR_DECISION = range(5)

# ==========================================
# UTILIDADES Y HELPER FUNCTIONS
# ==========================================
def obtener_auditoria(context: ContextTypes.DEFAULT_TYPE) -> RegistroAuditoria:
    """Registro de prompts y respuestas de la conversación actual (se crea al usarlo)."""
    return context.user_data.setdefault('audit_log', RegistroAuditoria())

def componer_mensaje_final(resultado) -> str:
    """Mensaje del pipeline más sus avisos (texto plano; se escapan si el mensaje va en HTML)."""
    avisos = [html.escape(a) if resultado.mensaje_html else a for a in resultado.avisos]
    return "\n\n".join([resultado.mensaje] + [f"⚠️ {a}" for a in avisos])

def obtener_config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    """Configuración validada que main() dejó en bot_data al arrancar."""
    return context.bot_data['config']

def obtener_cliente_ia(context: ContextTypes.DEFAULT_TYPE) -> ClienteIA:
    """Cliente del proveedor de IA compartido por todas las conversaciones."""
    return context.bot_data['cliente_ia']

def obtener_bitacora(context: ContextTypes.DEFAULT_TYPE) -> Bitacora:
    """Registro de auditoría permanente compartido por todas las conversaciones."""
    return context.bot_data['bitacora']

def quien(context: ContextTypes.DEFAULT_TYPE, usuario_tg) -> dict:
    """Identificación del usuario para la bitácora."""
    uid = usuario_tg.id if usuario_tg else None
    usuario = obtener_config(context).usuario_telegram(uid) if uid is not None else None
    return {
        "canal": "telegram", "telegram_id": uid,
        "nombre": usuario.nombre if usuario else None, "rol": usuario.rol if usuario else None,
    }

def datos_ot(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Número y descripción de la OT cargada en la conversación, para la bitácora."""
    tr = (context.user_data.get('metadata_json') or {}).get('transport_request', {}) or {}
    return {"orden": tr.get('id'), "descripcion": tr.get('description')}

def guardar_huella_entrada(context: ContextTypes.DEFAULT_TYPE, clave: str, nombre: str, ruta: str):
    """Guarda nombre, tamaño y SHA-256 de un archivo subido (no su contenido)
    para registrarlos al iniciar la ejecución."""
    with open(ruta, 'rb') as f:
        context.user_data.setdefault('entradas', {})[clave] = huella_archivo(nombre, f.read())

async def loading_animation(status_msg, base_text, phase_key="loading_phrases_tech"):
    """Edita el mensaje de estado cada 4 s con una frase distinta mientras dura una
    fase de IA. Termina cuando se cancela la tarea."""
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

def progreso_en(status_msg):
    """Devuelve la función de progreso que espera el pipeline: anima
    'status_msg' mientras dura cada fase de IA."""
    @asynccontextmanager
    async def progreso(base_text):
        """Lanza la animación al entrar y la cancela al salir de la fase."""
        loader_task = asyncio.create_task(loading_animation(status_msg, base_text))
        try:
            yield
        finally:
            loader_task.cancel()
    return progreso

# ==========================================
# HANDLERS: FLUJO DE DOCUMENTOS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1 (/start): limpia la conversación anterior y pide el documento de
    calibración, con un botón para saltarlo."""
    context.user_data.clear()
    keyboard = [[InlineKeyboardButton(TEXTS[LANG]["btn_skip_calib"], callback_data='skip_calib')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(TEXTS[LANG]["start_v2"], reply_markup=reply_markup)
    return WAITING_CALIBRATION

async def skip_calibration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1 (botón 'Saltar'): sigue sin calibración; se usará el estilo por defecto."""
    query = update.callback_query
    await query.answer()
    context.user_data['calib_raw_text'] = None
    context.user_data.setdefault('entradas', {}).pop('calibracion', None)
    await query.edit_message_text(TEXTS[LANG]["msg_calib_skipped"], parse_mode='HTML')
    return WAITING_AFU

async def handle_calibration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 1: recibe el documento de calibración (.docx o .pdf), guarda su huella
    y su texto, y pasa a pedir el AFU."""
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
            guardar_huella_entrada(context, 'calibracion', document.file_name, file_path)
            context.user_data['calib_raw_text'] = extract_text_from_file(file_path)

        await status_msg.edit_text(TEXTS[LANG]["msg_calib_success"], parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error en Calibración: {e}")
        await status_msg.edit_text(TEXTS[LANG]["afu_error"])
    return WAITING_AFU

async def handle_afu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso 2: recibe el documento funcional (AFU), guarda su huella y su texto, y
    conserva el .docx original para el modo DTSS completo. Pasa a pedir el ZIP."""
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
            guardar_huella_entrada(context, 'afu', document.file_name, file_path)
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
    """Paso 3: recibe el ZIP de la OT, lo lee (metadata.json y fuentes .abap) y
    ofrece los botones: Code Review, solo técnica o técnica + funcional."""
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
            guardar_huella_entrada(context, 'zip_ot', document.file_name, zip_path)

            try:
                metadata_content, abap_files_dict = leer_zip_ot(zip_path)
            except ZipSinMetadataError:
                await status_msg.edit_text(TEXTS[LANG]["zip_missing_meta"])
                return WAITING_ZIP

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
    """Paso 4: según el botón, genera directamente o ejecuta antes el Code Review.
    Si el Code Review encuentra hallazgos, los muestra y espera la decisión."""
    query = update.callback_query
    await query.answer()
    status_msg = context.user_data['internal_status_msg']

    # Solo el botón "técnica + requisitos funcionales" activa el modo nuevo;
    # el resto de caminos (incluido el Code Review) se comporta como siempre.
    context.user_data['incluir_funcional'] = (query.data == 'skip_cr_full')

    if query.data in ('skip_cr', 'skip_cr_full'):
        context.user_data['code_review'] = {"estado": "omitido"}
        await query.edit_message_text(TEXTS[LANG]["msg_cr_skipped"])
        await execute_pipeline(update, context)
        return ConversationHandler.END

    await query.edit_message_text(TEXTS[LANG]["msg_cr_analyzing"])
    async with progreso_en(status_msg)(TEXTS[LANG]["msg_cr_analyzing"]):
        code_review = await revisar_codigo(
            obtener_cliente_ia(context), obtener_config(context).modelos.fase1_5,
            context.user_data['metadata_str'], context.user_data['abap_code_str'],
            obtener_auditoria(context),
        )

    hallazgos = len(code_review.get("findings", []) or [])
    if code_review.get("ilegible"):
        context.user_data['code_review'] = {"estado": "ilegible"}
    elif code_review.get("has_violations", False):
        context.user_data['code_review'] = {"estado": "con_hallazgos", "hallazgos": hallazgos}
    else:
        context.user_data['code_review'] = {"estado": "sin_hallazgos"}

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
    """Paso 5: tras ver los hallazgos, el usuario los ignora (se genera el documento
    y queda registrado) o cancela (vuelve a esperar un ZIP)."""
    query = update.callback_query
    await query.answer()
    if query.data == 'ignore_cr':
        context.user_data['code_review']["estado"] = "hallazgos_ignorados"
        await query.edit_message_text(TEXTS[LANG]["msg_cr_skipped"])
        await execute_pipeline(update, context)
        return ConversationHandler.END
    else:
        obtener_bitacora(context).registrar(
            "code_review_cancelado", usuario=quien(context, update.effective_user), ot=datos_ot(context),
            code_review=context.user_data.get('code_review'),
        )
        await query.edit_message_text(TEXTS[LANG]["msg_process_cancelled"])
        return WAITING_ZIP

# ==========================================
# EJECUCIÓN DEL PIPELINE Y ENVÍO DE RESULTADOS
# ==========================================
async def execute_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paso final: registra el inicio en la bitácora, ejecuta el caso de uso con
    animación de progreso, registra el resultado y envía al usuario el log, el
    Word, los CSV y el JSON, borrando cada archivo temporal tras enviarlo."""
    status_msg = context.user_data['internal_status_msg']
    auditoria = obtener_auditoria(context)
    resultado = None
    bitacora = obtener_bitacora(context)
    config = obtener_config(context)
    id_ejecucion = uuid.uuid4().hex
    inicio = time.monotonic()
    bitacora.registrar(
        "ejecucion_iniciada", id_ejecucion=id_ejecucion, usuario=quien(context, update.effective_user),
        ot=datos_ot(context), entradas=context.user_data.get('entradas', {}),
        modo="tecnica_y_funcional" if context.user_data.get('incluir_funcional') else "tecnica",
        code_review=context.user_data.get('code_review'),
        ia={"proveedor": config.llm_provider, "modelos": asdict(config.modelos)},
    )

    try:
        entrada = EntradaPipeline(
            metadata_json=context.user_data['metadata_json'],
            abap_dict=context.user_data['abap_dict'],
            calib_raw_text=context.user_data.get('calib_raw_text'),
            afu_raw_text=context.user_data.get('afu_raw_text'),
            afu_docx_bytes=context.user_data.get('afu_docx_bytes'),
            incluir_funcional=context.user_data.get('incluir_funcional', False),
        )
        resultado = await ejecutar_pipeline(
            entrada, obtener_cliente_ia(context), config.modelos, auditoria, context.bot_data['generador'],
            progreso=progreso_en(status_msg),
        )
        await status_msg.edit_text(
            componer_mensaje_final(resultado), parse_mode='HTML' if resultado.mensaje_html else None
        )

    except Exception as e:
        logger.error(f"Error Pipeline: {e}")
        await status_msg.edit_text(TEXTS[LANG].get("zip_error", "Error interno") + f"\nDetalle: {e}")

    finally:
        registrar_fin_ejecucion(bitacora, id_ejecucion, resultado, time.monotonic() - inicio)
        try:
            file_ts = get_file_timestamp()

            with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.txt', encoding='utf-8') as tmp_file:
                tmp_file.write(auditoria.texto())
                tmp_path = tmp_file.name
            await context.bot.send_document(
                chat_id=status_msg.chat_id,
                document=open(tmp_path, 'rb'),
                filename=f"Auditoria_Agentes_Log_{file_ts}.txt",
                caption=TEXTS[LANG]["msg_log_caption"]
            )
            os.remove(tmp_path)

            if resultado and resultado.docx_path and os.path.exists(resultado.docx_path):
                es_completo = context.user_data.get('incluir_funcional', False)
                await context.bot.send_document(
                    chat_id=status_msg.chat_id,
                    document=open(resultado.docx_path, 'rb'),
                    filename=f"DTSS_Completo_{file_ts}.docx" if es_completo else f"DTSS_{file_ts}.docx",
                    caption=TEXTS[LANG]["msg_full_dtss_caption"] if es_completo else TEXTS[LANG]["msg_draw_doc"]
                )
                os.remove(resultado.docx_path)

            for ruta_csv in (resultado.csvs_configuracion if resultado else []):
                if os.path.exists(ruta_csv):
                    await context.bot.send_document(
                        chat_id=status_msg.chat_id,
                        document=open(ruta_csv, 'rb'),
                        filename=os.path.basename(ruta_csv),
                        caption=TEXTS[LANG]["msg_csv_config"]
                    )
                    os.remove(ruta_csv)

            # El JSON se conserva siempre como respaldo/auditoría, incluso si el .docx sí se generó
            if resultado and resultado.borrador_json:
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json', encoding='utf-8') as tmp_json:
                    tmp_json.write(resultado.borrador_json)
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
            bitacora.registrar("envio_fallido", id_ejecucion=id_ejecucion, error=f"{type(log_e).__name__}: {log_e}")

def registrar_fin_ejecucion(bitacora: Bitacora, id_ejecucion: str, resultado, duracion_s: float):
    """Huellas de lo generado, antes de enviarlo y borrarlo del disco."""
    salidas = {}
    if resultado and resultado.docx_path and os.path.exists(resultado.docx_path):
        salidas["docx_sha256"] = sha256_archivo(resultado.docx_path)
    if resultado and resultado.borrador_json:
        salidas["json_sha256"] = sha256_bytes(resultado.borrador_json.encode("utf-8"))
    csvs = [r for r in (resultado.csvs_configuracion if resultado else []) if os.path.exists(r)]
    if csvs:
        salidas["csv_sha256"] = {os.path.basename(r): sha256_archivo(r) for r in csvs}
    if resultado is None:
        estado = "error"
    elif "docx_sha256" in salidas:
        estado = "documento_generado"
    elif resultado.borrador_json:
        estado = "solo_json"  # el Word falló pero el contenido se generó
    else:
        estado = "error"
    bitacora.registrar(
        "ejecucion_finalizada", id_ejecucion=id_ejecucion, estado=estado, salidas=salidas,
        avisos=list(resultado.avisos) if resultado else [],
        error=None if (resultado is None or estado != "error") else resultado.mensaje,
        duracion_s=round(duracion_s, 1),
    )

# ==========================================
# GESTIÓN GLOBAL Y ARRANQUE
# ==========================================
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel: abandona la conversación en curso y borra lo que se había subido."""
    await update.message.reply_text(TEXTS[LANG]["msg_global_cancel"])
    context.user_data.clear()
    return ConversationHandler.END

async def handle_unexpected_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Atrapa cualquier texto o imagen enviada por error o fuera de flujo."""
    mensaje = "⚠️ <b>Entrada no reconocida.</b>\n\nEstoy esperando que subas un archivo (.docx, .pdf, o .zip) o presiones un botón.\n\nSi deseas iniciar un nuevo análisis, escribe /start"
    await update.message.reply_text(mensaje, parse_mode='HTML')

async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Los cortes de red con Telegram (502, timeouts) se reintentan solos:
    basta una línea de aviso. Cualquier otro error se registra con su traza."""
    error = context.error
    # BadRequest hereda de NetworkError pero es un fallo de la petición, no de la red.
    if isinstance(error, NetworkError) and not isinstance(error, BadRequest):
        logger.warning(f"Telegram no respondió ({error}); se reintenta automáticamente.")
        return
    logger.error("Error no controlado en el bot", exc_info=error)

async def control_acceso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Primer filtro de cada update: solo chats privados de usuarios del
    archivo de usuarios permitidos. Lo que no pasa se registra y se corta aquí,
    antes de que ningún handler descargue archivos o llame a la IA."""
    usuario = update.effective_user
    chat = update.effective_chat
    uid = usuario.id if usuario else None

    if chat is not None and chat.type != ChatType.PRIVATE:
        logger.warning(f"Acceso denegado: chat {chat.type} {chat.id} (usuario {uid}). El bot abandona el chat.")
        obtener_bitacora(context).registrar(
            "acceso_denegado", motivo="chat_no_privado", canal="telegram", telegram_id=uid,
            chat={"tipo": str(chat.type), "id": chat.id},
        )
        try:
            await chat.leave()
        except Exception as e:
            logger.warning(f"No se pudo abandonar el chat {chat.id}: {e}")
        raise ApplicationHandlerStop

    if uid is None or obtener_config(context).usuario_telegram(uid) is None:
        nombre = f"@{usuario.username}" if usuario and usuario.username else "sin alias"
        logger.warning(f"Acceso denegado: usuario {uid} ({nombre}).")
        obtener_bitacora(context).registrar(
            "acceso_denegado", motivo="usuario_no_autorizado", canal="telegram", telegram_id=uid,
            alias=usuario.username if usuario else None,
        )
        if uid is not None:
            mensaje = TEXTS[LANG]["msg_no_autorizado"].format(user_id=uid)
            if update.callback_query:
                await update.callback_query.answer(mensaje, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(mensaje)
        raise ApplicationHandlerStop

def construir_app(config: Config) -> Application:
    """Crea la aplicación de Telegram: comparte configuración, cliente de IA,
    bitácora y generador de Word en bot_data, registra el control de acceso antes
    que nada y define la conversación paso a paso."""
    app = (
        Application.builder()
        .token(config.telegram_token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .build()
    )

    app.bot_data['config'] = config
    app.bot_data['cliente_ia'] = crear_cliente_ia(config)
    app.bot_data['bitacora'] = Bitacora(config.auditoria_path)
    app.bot_data['generador'] = GeneradorWord(config.plantilla_path)

    # Grupo -1: corre antes que cualquier otro handler y puede cortar el update.
    app.add_handler(TypeHandler(Update, control_acceso), group=-1)

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
    app.add_error_handler(manejar_error)
    # MANEJADOR GLOBAL POR SI HABLAN FUERA DE UNA CONVERSACIÓN
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unexpected_input))
    return app

def main():
    """Punto de entrada (python -m sapdoc o sapdoc-telegram): valida la configuración,
    construye la aplicación y escucha mensajes por long-polling."""
    try:
        config = cargar_config()
    except ConfigError as e:
        logger.error(e)
        return

    app = construir_app(config)
    if config.usuarios:
        logger.info(f"Usuarios autorizados ({config.usuarios_path}): {len(config.usuarios)}.")
    else:
        logger.warning(
            f"No hay usuarios autorizados en {config.usuarios_path}: nadie puede usar el bot. Escríbele "
            "desde Telegram para ver tu ID y agrégalo al archivo (ver usuarios_permitidos.example.json)."
        )
    logger.info(f"Registro de auditoría: {config.auditoria_path}")
    logger.info(TEXTS[LANG]["log_bot_listening"])
    app.run_polling()

if __name__ == "__main__":
    main()
