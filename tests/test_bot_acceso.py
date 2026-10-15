"""Control de acceso del canal Telegram: lista blanca y solo chats privados."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, TypeHandler

import bot
from config import cargar_config

PLANTILLA = str(Path(__file__).resolve().parent.parent / "plantilla_dtss.docx")
AUTORIZADO, EXTRANO = 111, 999


def config(permitidos=str(AUTORIZADO)):
    return cargar_config({
        "TELEGRAM_BOT_TOKEN": "123:abc", "LLM_API_KEY": "k", "PLANTILLA_DTSS_PATH": PLANTILLA,
        "TELEGRAM_USUARIOS_PERMITIDOS": permitidos,
    })


class Registro:
    """Anota las respuestas que el bot intenta enviar."""

    def __init__(self):
        self.respuestas, self.alertas, self.abandonos = [], [], []

    async def reply_text(self, texto):
        self.respuestas.append(texto)

    async def answer(self, texto, show_alert=False):
        self.alertas.append((texto, show_alert))

    async def leave(self):
        self.abandonos.append(True)


def update_falso(uid, tipo_chat=ChatType.PRIVATE, boton=False, registro=None):
    r = registro or Registro()
    usuario = SimpleNamespace(id=uid, username="alguien") if uid is not None else None
    chat = SimpleNamespace(id=-5 if tipo_chat != ChatType.PRIVATE else uid, type=tipo_chat, leave=r.leave)
    return SimpleNamespace(
        effective_user=usuario, effective_chat=chat,
        callback_query=SimpleNamespace(answer=r.answer) if boton else None,
        effective_message=SimpleNamespace(reply_text=r.reply_text),
    ), r


def correr(update, permitidos=str(AUTORIZADO)):
    contexto = SimpleNamespace(bot_data={"config": config(permitidos)})
    return asyncio.run(bot.control_acceso(update, contexto))


def test_usuario_autorizado_en_chat_privado_pasa():
    update, r = update_falso(AUTORIZADO)
    assert correr(update) is None
    assert (r.respuestas, r.alertas, r.abandonos) == ([], [], [])


def test_usuario_no_autorizado_se_corta_y_recibe_su_id():
    update, r = update_falso(EXTRANO)
    with pytest.raises(ApplicationHandlerStop):
        correr(update)
    assert len(r.respuestas) == 1 and str(EXTRANO) in r.respuestas[0]


def test_boton_de_usuario_no_autorizado_responde_con_alerta():
    update, r = update_falso(EXTRANO, boton=True)
    with pytest.raises(ApplicationHandlerStop):
        correr(update)
    assert r.respuestas == [] and r.alertas[0][1] is True


@pytest.mark.parametrize("tipo", [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL])
def test_en_grupos_y_canales_el_bot_sale_aunque_el_usuario_este_autorizado(tipo):
    update, r = update_falso(AUTORIZADO, tipo_chat=tipo)
    with pytest.raises(ApplicationHandlerStop):
        correr(update)
    assert r.abandonos == [True] and r.respuestas == []


def test_lista_vacia_no_autoriza_a_nadie():
    update, _ = update_falso(AUTORIZADO)
    with pytest.raises(ApplicationHandlerStop):
        correr(update, permitidos="")


def test_update_sin_usuario_se_corta_sin_responder():
    update, r = update_falso(None)
    with pytest.raises(ApplicationHandlerStop):
        correr(update)
    assert r.respuestas == [] and r.alertas == []


def test_el_control_se_registra_antes_que_cualquier_handler():
    app = bot.construir_app(config())
    primeros = app.handlers[-1]
    assert min(app.handlers) == -1
    assert len(primeros) == 1
    assert isinstance(primeros[0], TypeHandler) and primeros[0].type is Update
    assert primeros[0].callback is bot.control_acceso
