"""Fechas en el formato que usan los logs y los nombres de archivo."""

from datetime import datetime


def get_current_timestamp():
    """Fecha y hora legible para el registro de auditoría (AAAA-MM-DD hh:mm:ss)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_file_timestamp():
    """Fecha y hora apta para nombres de archivo (AAAAMMDD_hhmmss)."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")
