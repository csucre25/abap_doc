"""Cómo el canal Telegram presenta el resultado del pipeline."""
import bot
from pipeline import ResultadoPipeline


def test_sin_avisos_el_mensaje_queda_igual():
    assert bot.componer_mensaje_final(ResultadoPipeline(mensaje="<b>Listo</b>")) == "<b>Listo</b>"


def test_los_avisos_se_agregan_y_se_escapan_en_html():
    resultado = ResultadoPipeline(mensaje="<b>Listo</b>", avisos=["Calibración <no> usada"])
    assert bot.componer_mensaje_final(resultado) == "<b>Listo</b>\n\n⚠️ Calibración &lt;no&gt; usada"


def test_en_texto_plano_los_avisos_no_se_escapan():
    resultado = ResultadoPipeline(mensaje="Error", mensaje_html=False, avisos=["a < b"])
    assert bot.componer_mensaje_final(resultado) == "Error\n\n⚠️ a < b"
