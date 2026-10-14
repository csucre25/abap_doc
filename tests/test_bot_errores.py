"""El manejador global de errores del canal Telegram."""
import asyncio
import logging
from types import SimpleNamespace

from telegram.error import BadRequest, NetworkError, TimedOut

import bot


def _manejar(error, caplog):
    with caplog.at_level(logging.WARNING, logger=bot.logger.name):
        asyncio.run(bot.manejar_error(None, SimpleNamespace(error=error)))
    return caplog.records


def test_corte_de_red_es_un_aviso_de_una_linea(caplog):
    for error in (NetworkError("Bad Gateway"), TimedOut()):
        caplog.clear()
        (registro,) = _manejar(error, caplog)
        assert registro.levelno == logging.WARNING
        assert registro.exc_info is None
        assert "se reintenta" in registro.getMessage()


def test_otros_errores_conservan_la_traza(caplog):
    for error in (ValueError("fallo real"), BadRequest("mensaje inválido")):
        caplog.clear()
        (registro,) = _manejar(error, caplog)
        assert registro.levelno == logging.ERROR
        assert registro.exc_info[1] is error
