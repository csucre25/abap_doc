"""Fechas en el formato que usan los logs y los nombres de archivo."""

from datetime import datetime


def get_current_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_file_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")
