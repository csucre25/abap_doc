"""Reglas para decidir qué objetos de la OT pasan por IA y cómo se presenta
el código de cada uno. Todo se calcula a partir de metadata.json y del fuente,
sin llamadas externas."""

import difflib

from sapdoc.domain.ruido_sap import is_technical_noise

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
    """Entrada de 'objetos_omitidos_ruido': el objeto autogenerado por SAP y el
    motivo por el que no se documenta."""
    return {
        "nombre": obj.get("object_name", ""),
        "tipo": obj.get("object_type", ""),
        "motivo": "Generado automáticamente por SAP, sin lógica propia."
    }
