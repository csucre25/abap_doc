"""Extracción de texto de los documentos que sube el usuario (PDF o Word)."""

import pymupdf as fitz
from docx import Document


def extract_text_from_file(file_path: str) -> str:
    """Extrae el texto de un PDF o DOCX en milisegundos."""
    extension = file_path.lower().split('.')[-1]
    if extension == 'docx':
        doc = Document(file_path)
        raw_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
    elif extension == 'pdf':
        raw_text = ""
        with fitz.open(file_path) as pdf:
            for page in pdf:
                raw_text += page.get_text() + "\n"
    else:
        raise ValueError("Formato no soportado.")
    return raw_text[:15000]
