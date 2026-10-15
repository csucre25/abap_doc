import os
import json
import logging
import tempfile
import html
import asyncio
import random
from contextlib import asynccontextmanager
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application, ApplicationHandlerStop, CommandHandler, MessageHandler, CallbackQueryHandler,
    TypeHandler, filters, ContextTypes, ConversationHandler
)
from config import Config, ConfigError, cargar_config
from ia import ClienteIA, crear_cliente_ia
from document_parser import extract_text_from_file
from messages import TEXTS
from pipeline import (
    EntradaPipeline, RegistroAuditoria, ZipSinMetadataError,
    ejecutar_pipeline, revisar_codigo, leer_zip_ot, get_file_timestamp,
)

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
    return context.user_data.setdefault('audit_log', RegistroAuditoria())

def componer_mensaje_final(resultado) -> str:
    """Mensaje del pipeline más sus avisos (texto plano; se escapan si el mensaje va en HTML)."""
    avisos = [html.escape(a) if resultado.mensaje_html else a for a in resultado.avisos]
    return "\n\n".join([resultado.mensaje] + [f"⚠️ {a}" for a in avisos])

def obtener_config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data['config']

def obtener_cliente_ia(context: ContextTypes.DEFAULT_TYPE) -> ClienteIA:
    return context.bot_data['cliente_ia']

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

def progreso_en(status_msg):
    """Devuelve la función de progreso que espera el pipeline: anima
    'status_msg' mientras dura cada fase de IA."""
    @asynccontextmanager
    async def progreso(base_text):
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
    async with progreso_en(status_msg)(TEXTS[LANG]["msg_cr_analyzing"]):
        code_review = await revisar_codigo(
            obtener_cliente_ia(context), obtener_config(context).modelos.fase1_5,
            context.user_data['metadata_str'], context.user_data['abap_code_str'],
            obtener_auditoria(context),
        )

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
# EJECUCIÓN DEL PIPELINE Y ENVÍO DE RESULTADOS
# ==========================================
async def execute_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = context.user_data['internal_status_msg']
    auditoria = obtener_auditoria(context)
    resultado = None

    try:
        entrada = EntradaPipeline(
            metadata_json=context.user_data['metadata_json'],
            abap_dict=context.user_data['abap_dict'],
            calib_raw_text=context.user_data.get('calib_raw_text'),
            afu_raw_text=context.user_data.get('afu_raw_text'),
            afu_docx_bytes=context.user_data.get('afu_docx_bytes'),
            incluir_funcional=context.user_data.get('incluir_funcional', False),
        )
        config = obtener_config(context)
        resultado = await ejecutar_pipeline(
            entrada, obtener_cliente_ia(context), config.modelos, auditoria,
            progreso=progreso_en(status_msg), plantilla_path=config.plantilla_path,
        )
        await status_msg.edit_text(
            componer_mensaje_final(resultado), parse_mode='HTML' if resultado.mensaje_html else None
        )

    except Exception as e:
        logger.error(f"Error Pipeline: {e}")
        await status_msg.edit_text(TEXTS[LANG].get("zip_error", "Error interno") + f"\nDetalle: {e}")

    finally:
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
    """Primer filtro de cada update: solo chats privados de usuarios en
    TELEGRAM_USUARIOS_PERMITIDOS. Lo que no pasa se registra y se corta aquí,
    antes de que ningún handler descargue archivos o llame a la IA."""
    usuario = update.effective_user
    chat = update.effective_chat
    uid = usuario.id if usuario else None

    if chat is not None and chat.type != ChatType.PRIVATE:
        logger.warning(f"Acceso denegado: chat {chat.type} {chat.id} (usuario {uid}). El bot abandona el chat.")
        try:
            await chat.leave()
        except Exception as e:
            logger.warning(f"No se pudo abandonar el chat {chat.id}: {e}")
        raise ApplicationHandlerStop

    if uid is None or uid not in obtener_config(context).telegram_usuarios_permitidos:
        nombre = f"@{usuario.username}" if usuario and usuario.username else "sin alias"
        logger.warning(f"Acceso denegado: usuario {uid} ({nombre}).")
        if uid is not None:
            mensaje = TEXTS[LANG]["msg_no_autorizado"].format(user_id=uid)
            if update.callback_query:
                await update.callback_query.answer(mensaje, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(mensaje)
        raise ApplicationHandlerStop

def construir_app(config: Config) -> Application:
    app = (
        Application.builder()
        .token(config.telegram_token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .build()
    )

    app.bot_data['config'] = config
    app.bot_data['cliente_ia'] = crear_cliente_ia(config)

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
    try:
        config = cargar_config()
    except ConfigError as e:
        logger.error(e)
        return

    app = construir_app(config)
    if config.telegram_usuarios_permitidos:
        logger.info(f"Usuarios autorizados: {len(config.telegram_usuarios_permitidos)}.")
    else:
        logger.warning(
            "TELEGRAM_USUARIOS_PERMITIDOS está vacía: nadie puede usar el bot. Escríbele desde Telegram "
            "para ver tu ID y agrégalo en el .env."
        )
    logger.info(TEXTS[LANG]["log_bot_listening"])
    app.run_polling()

if __name__ == "__main__":
    main()
