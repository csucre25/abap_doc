# plantilla.py
#
# Arma el diccionario de contexto que espera "plantilla_dtss.docx" a partir
# de dtss_final (lo que ya arma execute_pipeline en bot.py) y lo renderiza
# con docxtpl para producir el .docx final.
#
# Requiere: pip install docxtpl Pillow --break-system-packages
# (Pillow se usa para renderizar las tablas de Configuración como imagen;
#  si no está disponible, se cae automáticamente a tabla nativa de Word.)

import os
import csv
import json
import tempfile
import traceback
import html
from datetime import datetime
from docx.shared import Cm
from docxtpl import DocxTemplate

# Plantilla por defecto cuando no se pasa 'plantilla_path'. La ruta
# configurable (PLANTILLA_DTSS_PATH) se resuelve en config.py.
PLANTILLA_PATH = "plantillas/plantilla_dtss.docx"

# Etiquetas legibles para el "badge" de tipo de objeto en la tabla.
LABELS_TIPO = {
    "TABL": "TABLA",
    "DTEL": "ELEMENTO DE DATOS",
    "DOMA": "DOMINIO",
    "VIEW": "VISTA",
    "SHLP": "AYUDA DE BÚSQUEDA",
    "TRAN": "TRANSACCIÓN",
    "TABU": "CUSTOMIZING",
    "PROG": "PROGRAMA",
    "CLAS": "CLASE ABAP",
    "FUNC": "MÓDULO DE FUNCIÓN",
    "FUGR": "GRUPO DE FUNCIONES",
    "ENHO": "ENHANCEMENT",
    "METH": "MÉTODO",
}


# La excepción la define la aplicación (puerto); aquí se lanza y afu.py la reutiliza.
from sapdoc.application.puertos import DocxBuildError  # noqa: E402,F401


def _tipo_label(tipo: str) -> str:
    """Etiqueta legible del tipo de objeto SAP (TABL → TABLA); si no se conoce, el código tal cual."""
    return LABELS_TIPO.get(tipo, tipo or "OBJETO")


def _codigo_a_texto(codigo) -> str:
    """codigo ya viene armado por bot.py como el código completo (objeto
    nuevo) o el diff (objeto modificado). Se admite también una lista por
    compatibilidad hacia atrás."""
    if isinstance(codigo, list):
        return "\n\n".join(str(c) for c in codigo) if codigo else "N/A"
    return codigo or "N/A"


def _campos_a_texto(campos) -> str:
    """Aplana 'campos' a texto legible. Soporta:
    - DDIC (field_name/type/length/rollname): tablas, elementos de datos, vistas.
    - Registros de customizing (TABU) ya estructurados por el extractor como
      dicts columna:valor (a partir de metadata.customizing.columns/rows).
    - Pares key/value simples con 'value' como JSON serializado (formato
      viejo del extractor, se mantiene por compatibilidad).
    - Pares key/value simples planos (fallback).
    NOTA: de momento se mantiene como texto plano (no tabla Word) mientras
    se valida la mecánica de Subdoc-por-objeto-en-loop con docxtpl real."""
    if not campos:
        return "Sin campos registrados."

    primero = campos[0]
    partes = []

    if isinstance(primero, dict) and ("field_name" in primero or "fieldname" in primero):
        for c in campos:
            nombre = c.get("field_name") or c.get("fieldname") or ""
            tipo = c.get("type") or c.get("datatype") or ""
            longitud = c.get("length") or c.get("leng") or ""
            rollname = c.get("rollname") or c.get("data_element") or ""
            partes.append(f"{nombre} | Tipo: {tipo} | Longitud: {longitud} | Tipo SAP: {rollname}")
        return "\n".join(partes)

    if isinstance(primero, dict) and ("key" in primero or "value" in primero):
        for c in campos:
            valor = c.get("value", "")
            fila_dict = None
            if isinstance(valor, str):
                try:
                    parsed = json.loads(valor)
                    if isinstance(parsed, dict):
                        fila_dict = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if fila_dict is not None:
                legible = " | ".join(f"{k}: {v}" for k, v in fila_dict.items())
                partes.append(f"{c.get('key', '')}: {legible}")
            else:
                partes.append(f"{c.get('key', '')}: {valor}".strip(": "))
        return "\n".join(partes)

    # Registros de customizing ya estructurados (dict columna:valor directo,
    # ej. {"campo": "GERENCIA", "low": "Ingeniería...", "high": "0876", ...}
    # armado en bot.py desde metadata.customizing.columns/rows).
    if isinstance(primero, dict):
        for c in campos:
            partes.append(" | ".join(f"{k}: {v}" for k, v in c.items() if v not in ("", None)))
        return "\n".join(partes)

    return "\n".join(str(c) for c in campos)


def _escapar_xml(valor):
    """Escapa &, < y > en cualquier string (recursivo en listas/dicts).
    OBLIGATORIO antes de insertar texto libre en un .docx vía docxtpl: el
    código ABAP trae field-symbols (<fs_algo>) y operadores de comparación
    (IF x > y, WHERE campo < valor) que, sin escapar, se interpretan como
    tags XML literales y corrompen el documento a partir de ese punto —
    el resto del contenido queda técnicamente en el archivo pero deja de
    renderizarse. El orden importa: '&' se reemplaza SIEMPRE primero."""
    if isinstance(valor, str):
        return valor.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if isinstance(valor, list):
        return [_escapar_xml(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _escapar_xml(v) for k, v in valor.items()}
    return valor


def construir_contexto(dtss_final: dict, metadata_json: dict,
                        cliente: str = "Cliente", proyecto: str = "Proyecto",
                        escapar_xml: bool = True) -> dict:
    # escapar_xml=True  → para docxtpl/Jinja (plantilla_dtss.docx), que inserta el
    #                     texto como XML crudo.
    # escapar_xml=False → para document_builder_afu.py, que escribe directo con
    #                     python-docx (ya escapa por su cuenta).
    """Convierte dtss_final y la metadata en el diccionario de variables que espera
    la plantilla (y que reutiliza el modo AFU): objetos de lógica y diccionario,
    configuración y órdenes de transporte."""
    objetos_logica = [
        {
            "object_type_label": _tipo_label(o.get("tipo", "")),
            "object_name": o.get("nombre", ""),
            "descripcion": o.get("justificacion") or "Sin justificación generada.",
            "explicacion": o.get("logica_tecnica") or "Sin explicación técnica generada.",
            "codigo_nuevo": _codigo_a_texto(o.get("codigo", {}).get("nuevo", "N/A") if isinstance(o.get("codigo"), dict) else "N/A"),
            "codigo_anterior": _codigo_a_texto(o.get("codigo", {}).get("anterior", "N/A") if isinstance(o.get("codigo"), dict) else "N/A"),
            "campos_resumen": _campos_a_texto(o.get("campos"))
        }
        for o in dtss_final.get("objetos_logica", [])
    ]

    objetos_diccionario = [
        {
            "object_type_label": _tipo_label(o.get("tipo", "")),
            "object_name": o.get("nombre", ""),
            "descripcion": o.get("descripcion_tecnica", ""),
            "campos_resumen": _campos_a_texto(o.get("campos")),
            "campos_crudos": o.get("campos"),
        }
        for o in dtss_final.get("objetos_diccionario", [])
    ]

    # Las constantes de negocio (ZTCONSTANTES y similares) llegan como
    # customizing en "configuracion"; no hay sección aparte de constantes.
    configuracion = dtss_final.get("configuracion", [])

    # metadata_json["transport_request"] hoy es un único dict (una sola OT).
    # Si más adelante el extractor consolida varias OTs en una lista, basta
    # con normalizar aquí antes de construir ordenes_transporte.
    tr = metadata_json.get("transport_request", {}) or {}
    ordenes_transporte = [{
        "orden": tr.get("id", ""),
        "usuario": tr.get("author", ""),
        "descripcion": tr.get("description", ""),
        "fecha": tr.get("date", ""),
    }] if tr else []

    contexto = {
        "cliente": cliente,
        "proyecto": proyecto,
        "version": "1.0",
        "fecha": datetime.now().strftime("%d/%m/%Y"),
        "objetos_logica": objetos_logica,
        "objetos_diccionario": objetos_diccionario,
    }
    if escapar_xml:
        contexto = _escapar_xml(contexto)

    # "ordenes_transporte" y "configuracion" NO se escapan: ya no se
    # sustituyen vía {{ }} de Jinja (ver renderizar_documento), se consumen
    # directo en Python para armar tablas/imágenes nativas (Subdoc), y
    # python-docx/matplotlib ya escapan correctamente por su cuenta —
    # escaparlos aquí también mostraría entidades XML literales (&amp;)
    # en el Word en vez del carácter real.
    contexto["ordenes_transporte"] = ordenes_transporte
    contexto["configuracion"] = configuracion
    return contexto


def _resumen_contexto(contexto: dict) -> str:
    """Resumen legible del contexto para el log: conteos + una muestra
    truncada de cada lista, en vez de volcar código fuente completo."""
    lineas = [f"cliente={contexto.get('cliente')!r}  proyecto={contexto.get('proyecto')!r}"]
    for clave in ["objetos_logica", "objetos_diccionario", "ordenes_transporte", "configuracion"]:
        valor = contexto.get(clave, [])
        lineas.append(f"- {clave}: {len(valor)} elemento(s)")
        if valor:
            try:
                muestra = json.dumps(valor[0], ensure_ascii=False, indent=2, default=str)
            except TypeError:
                muestra = str(valor[0])
            if len(muestra) > 600:
                muestra = muestra[:600] + "... [truncado]"
            lineas.append(f"  primer elemento:\n{muestra}")
    return "\n".join(lineas)


def _agregar_tabla_generica(subdoc, encabezados, filas):
    """Crea en el subdoc una tabla con encabezados fijos y N filas de datos."""
    table = subdoc.add_table(rows=len(filas) + 1, cols=len(encabezados))
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(encabezados):
        hdr_cells[i].text = str(h)
    for r, fila in enumerate(filas):
        row_cells = table.rows[r + 1].cells
        for c, valor in enumerate(fila):
            row_cells[c].text = "" if valor is None else str(valor)
    return table


def _reducir_columnas_constantes(columnas, filas):
    """Separa columnas CONSTANTES (mismo valor en todas las filas) de las
    que realmente VARÍAN. Devuelve (columnas_reducidas, filas_reducidas,
    nota_texto). No se pierde ningún dato: lo constante se resume en
    nota_texto, lo variable queda listo para tabla o imagen."""
    if not filas:
        return [], [], ""

    constantes = []
    variables_idx = []
    for i, col in enumerate(columnas):
        valores = {(fila[i] if i < len(fila) else "") for fila in filas}
        if len(valores) <= 1:
            valor_unico = next(iter(valores)) if valores else ""
            if valor_unico not in ("", None):
                constantes.append((col, valor_unico))
            # columnas constantes y vacías se descartan sin más (ruido)
        else:
            variables_idx.append(i)

    nota_texto = ""
    if constantes:
        nota_texto = "Válido para: " + ", ".join(f"{c.upper()}={v}" for c, v in constantes)

    columnas_reducidas = [columnas[i] for i in variables_idx]
    filas_reducidas = [[fila[i] if i < len(fila) else "" for i in variables_idx] for fila in filas]
    return columnas_reducidas, filas_reducidas, nota_texto


FUENTES_MONOESPACIADAS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "DejaVuSansMono.ttf",
    "LiberationMono-Regular.ttf",
    "cour.ttf",  # Courier New en Windows
]
FUENTES_MONOESPACIADAS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "DejaVuSansMono-Bold.ttf",
    "courbd.ttf",
] + FUENTES_MONOESPACIADAS


def _cargar_fuente(tamano, negrita=False):
    """Primera fuente monoespaciada disponible en el sistema; si no hay ninguna,
    la fuente bitmap por defecto de Pillow."""
    from PIL import ImageFont
    for ruta in (FUENTES_MONOESPACIADAS_BOLD if negrita else FUENTES_MONOESPACIADAS):
        try:
            return ImageFont.truetype(ruta, tamano)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()  # último recurso: fuente bitmap pequeña


def _generar_imagen_tabla(columnas, filas, ruta_png, tamano_fuente=20):
    """Renderiza una tabla como PNG usando Pillow (sin dependencias pesadas
    tipo matplotlib/Chrome). El usuario puede agrandar la imagen en Word sin
    perder legibilidad — a diferencia de una tabla nativa muy ancha o con
    pocas filas y muchas columnas, que fuerza texto vertical/ilegible.

    Requiere: Pillow (pip install Pillow --break-system-packages). Lanza la
    excepción tal cual si algo falla — el llamador (renderizar_documento)
    decide el fallback a tabla nativa."""
    from PIL import Image, ImageDraw

    fuente = _cargar_fuente(tamano_fuente)
    fuente_bold = _cargar_fuente(tamano_fuente, negrita=True)
    padding = 12
    alto_fila = int(tamano_fuente * 1.7)

    encabezados = [str(c) for c in columnas]
    filas_texto = [[str(v) if v not in (None, "") else "" for v in fila] for fila in filas]

    tmp = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp)

    def ancho_texto(texto, f):
        """Ancho en píxeles que ocupa un texto con la fuente dada."""
        bbox = tmp_draw.textbbox((0, 0), texto, font=f)
        return bbox[2] - bbox[0]

    anchos_col = []
    for i, h in enumerate(encabezados):
        max_ancho = ancho_texto(h, fuente_bold)
        for f in filas_texto:
            max_ancho = max(max_ancho, ancho_texto(f[i], fuente))
        anchos_col.append(max_ancho + 20)

    ancho_total = sum(anchos_col) + padding * 2
    alto_total = (len(filas_texto) + 1) * alto_fila + padding * 2

    img = Image.new("RGB", (ancho_total, alto_total), "white")
    draw = ImageDraw.Draw(img)

    y = padding
    draw.rectangle([padding, y, ancho_total - padding, y + alto_fila], fill="#1F4E78")
    cx = padding
    for i, h in enumerate(encabezados):
        draw.text((cx + 8, y + 5), h, font=fuente_bold, fill="white")
        cx += anchos_col[i]
    y += alto_fila

    for r, fila in enumerate(filas_texto):
        color = "#F2F2F2" if r % 2 == 0 else "white"
        draw.rectangle([padding, y, ancho_total - padding, y + alto_fila], fill=color)
        cx = padding
        for i, val in enumerate(fila):
            draw.text((cx + 8, y + 5), val, font=fuente, fill="black")
            cx += anchos_col[i]
        y += alto_fila

    cx = padding
    for w in anchos_col:
        draw.line([(cx, padding), (cx, alto_total - padding)], fill="#CCCCCC")
        cx += w
    draw.line([(cx, padding), (cx, alto_total - padding)], fill="#CCCCCC")
    for r in range(len(filas_texto) + 2):
        yy = padding + r * alto_fila
        draw.line([(padding, yy), (ancho_total - padding, yy)], fill="#CCCCCC")

    img.save(ruta_png, format="PNG")
    return ruta_png


def exportar_csv_configuracion(cfg, output_dir):
    """Exporta el customizing COMPLETO (todas las columnas originales, sin
    compactar) a un .csv — pensado como respaldo para que el usuario pueda
    validar o recargar la configuración fuera del Word. No depende de
    pandas/dataframe_image; usa solo el módulo estándar csv."""
    nombre = cfg.get("nombre", "configuracion")
    ruta = os.path.join(output_dir, f"{nombre}_customizing.csv")
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cfg.get("columnas", []))
        writer.writerows(cfg.get("filas", []))
    return ruta


def _tabla_configuracion_compacta(subdoc, columnas, filas):
    """Fallback de tabla nativa (sin imagen) para cuando la generación de
    imagen no está disponible o falla. Reduce columnas constantes igual
    que la vía de imagen, pero pinta una tabla Word normal."""
    if not filas:
        subdoc.add_paragraph("Sin registros de customizing.")
        return

    columnas_reducidas, filas_reducidas, nota_texto = _reducir_columnas_constantes(columnas, filas)
    if nota_texto:
        p = subdoc.add_paragraph()
        p.add_run(nota_texto).italic = True
    if not columnas_reducidas:
        return  # todas las columnas eran constantes, ya quedaron en la nota
    _agregar_tabla_generica(subdoc, columnas_reducidas, filas_reducidas)


# def _construir_tabla_campos_subdoc(doc, campos):
#     """Arma un Subdoc con la tabla de campos/atributos de un objeto,
#     detectando automáticamente qué forma traen los datos:

#     - Campos DDIC (TABL/DTEL/VIEW, o ddic_fields de un objeto con lógica):
#       dicts con field_name/type/length/rollname → tabla estilo SE11
#       (Campo | Tipo de Dato | Longitud | Tipo SAP).
#     - Registros de customizing real (TABU, ej. ZTCONSTANTES) cuyo 'value'
#       es un JSON serializado de la fila completa → se expande a una tabla
#       con una columna por cada campo real del registro.
#     - Pares key/value simples (fallback) → tabla de 2 columnas.

#     NOTA: no está conectado en renderizar_documento por ahora (ver historial
#     de conversación: el patrón Subdoc-por-objeto-dentro-de-un-loop de Jinja
#     salió vacío en pruebas reales y quedó pausado). Se deja implementado y
#     probado por si se retoma más adelante con un mecanismo distinto al loop.
#     """
#     subdoc = doc.new_subdoc()
#     if not campos:
#         subdoc.add_paragraph("Sin campos registrados.")
#         return subdoc 

#     primero = campos[0]

#     # Caso 1: campos DDIC (tablas, elementos de datos, vistas, estructuras)
#     if isinstance(primero, dict) and ("field_name" in primero or "fieldname" in primero):
#         filas = []
#         for c in campos:
#             filas.append([
#                 c.get("field_name") or c.get("fieldname") or "",
#                 c.get("type") or c.get("datatype") or "",
#                 c.get("length") or c.get("leng") or "",
#                 c.get("rollname") or c.get("data_element") or "",
#             ])
#         _agregar_tabla_generica(subdoc, ["Campo", "Tipo de Dato", "Longitud", "Tipo SAP"], filas)
#         return subdoc

#     # Caso 2: registros de customizing (TABU) — 'value' trae un JSON con la
#     # fila completa (ej. {"campo":"GERENCIA","low":"Suministros",...})
#     if isinstance(primero, dict) and ("key" in primero or "value" in primero):
#         registros = []
#         columnas_orden = []
#         columnas_vistas = set()
#         todo_es_json = True
#         for c in campos:
#             valor = c.get("value", "")
#             fila_dict = None
#             if isinstance(valor, str):
#                 try:
#                     parsed = json.loads(valor)
#                     if isinstance(parsed, dict):
#                         fila_dict = parsed
#                 except (json.JSONDecodeError, TypeError):
#                     pass
#             if fila_dict is None:
#                 todo_es_json = False
#                 break
#             registros.append(fila_dict)
#             for k in fila_dict.keys():
#                 if k not in columnas_vistas:
#                     columnas_vistas.add(k)
#                     columnas_orden.append(k)

#         if todo_es_json and registros:
#             filas = [[reg.get(col, "") for col in columnas_orden] for reg in registros]
#             _agregar_tabla_generica(subdoc, columnas_orden, filas)
#             return subdoc

#         # Fallback: pares key/value simples (no eran JSON de una fila completa)
#         filas = [[c.get("key", ""), c.get("value", "")] for c in campos]
#         _agregar_tabla_generica(subdoc, ["Campo", "Valor"], filas)
#         return subdoc

#     # Caso 3: forma desconocida — se listan las claves del primer dict tal cual
#     if isinstance(primero, dict):
#         columnas = list(primero.keys())
#         filas = [[c.get(col, "") for col in columnas] for c in campos]
#         _agregar_tabla_generica(subdoc, columnas, filas)
#         return subdoc

#     # Caso 4: ni siquiera son dicts — se listan como texto plano
#     for c in campos:
#         subdoc.add_paragraph(str(c))
#     return subdoc 

def _construir_tabla_campos_subdoc(doc, campos, imagenes_tmp_dir, prefijo_img):
    """Subdocumento con los campos de un objeto de diccionario: los convierte
    en filas (DDIC, customizing o clave/valor) y los dibuja como imagen;
    si la imagen falla, como tabla nativa."""
    subdoc = doc.new_subdoc()
    if not campos:
        subdoc.add_paragraph("Sin campos registrados.")
        return subdoc

    primero = campos[0]
    columnas = []
    filas = []

    # Caso 1: campos DDIC (Tablas, Vistas, Estructuras)
    if isinstance(primero, dict) and ("field_name" in primero or "fieldname" in primero):
        columnas = ["Campo", "Tipo", "Longitud", "Tipo SAP"]
        for c in campos:
            filas.append([
                html.unescape(str(c.get("field_name") or c.get("fieldname") or "")),
                html.unescape(str(c.get("type") or c.get("datatype") or "")),
                html.unescape(str(c.get("length") or c.get("leng") or "")),
                html.unescape(str(c.get("rollname") or c.get("data_element") or "")),
            ])
    # Caso 2 & Fallback: pares key/value simples
    elif isinstance(primero, dict) and ("key" in primero or "value" in primero):
        columnas = ["Atributo", "Valor"]
        for c in campos:
            valor = c.get("value", "")
            if isinstance(valor, str):
                try:
                    parsed = json.loads(html.unescape(valor))
                    if isinstance(parsed, dict):
                        valor = " | ".join(f"{k}: {v}" for k, v in parsed.items())
                except (json.JSONDecodeError, TypeError):
                    pass
            filas.append([html.unescape(str(c.get("key", ""))), html.unescape(str(valor))])
    # Caso 3: forma desconocida
    elif isinstance(primero, dict):
        columnas = list(primero.keys())
        filas = [[html.unescape(str(c.get(col, ""))) for col in columnas] for c in campos]
    else:
        for c in campos:
            subdoc.add_paragraph(html.unescape(str(c)))
        return subdoc

    try:
        ruta_png = os.path.join(imagenes_tmp_dir, f"{prefijo_img}.png")
        _generar_imagen_tabla(columnas, filas, ruta_png)
        subdoc.add_picture(ruta_png, width=Cm(10))
    except Exception:
        _agregar_tabla_generica(subdoc, columnas, filas)

    return subdoc


def renderizar_documento(contexto: dict, output_path: str, plantilla_path: str = None,
                          imagenes_tmp_dir: str = None) -> str:
    """Renderiza la plantilla con un contexto ya armado. Si falla, levanta
    DocxBuildError con: traceback real + variables que la plantilla espera
    vs las que se le mandaron + resumen del contexto usado — pensado para
    volcarse íntegro al log de auditoría, no solo mostrar str(e)."""
    plantilla_path = plantilla_path or PLANTILLA_PATH
    imagenes_tmp_dir = imagenes_tmp_dir or tempfile.gettempdir()
    doc = DocxTemplate(plantilla_path)

    # --- Subdoc: tabla de Órdenes de Transporte ---
    # IMPORTANTE: todos los subdocs deben ir en el MISMO contexto que el
    # resto de variables — nunca se debe llamar doc.render() más de una vez
    # sobre la misma instancia: la primera llamada ya reemplaza todo el XML
    # del documento por el resultado renderizado, así que una segunda
    # llamada no tiene ningún tag de Jinja real que sustituir.
    subdoc_ot = doc.new_subdoc()
    ordenes = contexto.get("ordenes_transporte", [])
    table = subdoc_ot.add_table(rows=len(ordenes) + 1, cols=4)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(["Orden", "Usuario", "Descripción", "Fecha"]):
        hdr_cells[i].text = h
    for i, ot in enumerate(ordenes):
        row_cells = table.rows[i + 1].cells
        row_cells[0].text = str(ot.get("orden", ""))
        row_cells[1].text = str(ot.get("usuario", ""))
        row_cells[2].text = str(ot.get("descripcion", ""))
        row_cells[3].text = str(ot.get("fecha", ""))

    contexto_final = dict(contexto)  # no mutar el dict que ya se logueó en bot.py
    contexto_final["tabla_ots"] = subdoc_ot

    # --- Subdoc: tabla(s) de Configuración (customizing real, ej. ZTCONSTANTES) ---
    # Mismo patrón que tabla_ots: UN solo Subdoc armado en Python con todo el
    # contenido en secuencia (encabezado + imagen/tabla por objeto), nunca un
    # Subdoc por objeto dentro de un loop de Jinja — eso fue lo que salió
    # vacío en pruebas reales.
    #
    # Cada tabla de customizing se compacta primero (columnas constantes →
    # nota de texto) y luego se intenta renderizar como IMAGEN de alta
    # resolución (el usuario puede agrandarla sin perder nitidez — más
    # legible que una tabla nativa muy ancha o con pocas filas y muchas
    # columnas, que fuerza texto vertical). Si dataframe_image/matplotlib no
    # están instalados o algo falla, se cae a la tabla nativa sin romper el
    # documento.
    subdoc_config = doc.new_subdoc()
    configuracion = contexto.get("configuracion", [])
    if not configuracion:
        subdoc_config.add_paragraph("No se identificaron objetos de customizing en esta OT.")
    for idx, cfg in enumerate(configuracion):
        p = subdoc_config.add_paragraph()
        p.add_run(f"{cfg.get('nombre', '')} — {cfg.get('descripcion', '')}").bold = True

        columnas = cfg.get("columnas", [])
        filas = cfg.get("filas", [])
        if not (columnas and filas):
            subdoc_config.add_paragraph("Sin registros de customizing.")
            subdoc_config.add_paragraph()
            continue

        columnas_reducidas, filas_reducidas, nota_texto = _reducir_columnas_constantes(columnas, filas)
        #if nota_texto:
            #p_nota = subdoc_config.add_paragraph()
            #p_nota.add_run(nota_texto).italic = True

        if columnas_reducidas:
            try:
            # Renderizamos la imagen con todas las columnas originales para mantener similitud con SAP ALV
                ruta_png = os.path.join(
                    imagenes_tmp_dir, f"config_{cfg.get('nombre', 'tabla')}_{idx}.png"
                )
                _generar_imagen_tabla(columnas, filas, ruta_png)
                subdoc_config.add_picture(ruta_png, width=Cm(17))
            except Exception as e:
                # Fallback a tabla nativa de Word (con reducción de columnas para evitar desbordamiento) solo si Pillow falla
                columnas_reducidas, filas_reducidas, nota_texto = _reducir_columnas_constantes(columnas, filas)
                if nota_texto:
                    p_nota = subdoc_config.add_paragraph()
                    p_nota.add_run(nota_texto).italic = True
                if columnas_reducidas:
                    _agregar_tabla_generica(subdoc_config, columnas_reducidas, filas_reducidas)
        subdoc_config.add_paragraph()  # espacio entre objetos de configuración

    # contexto_final["tabla_configuracion"] = subdoc_config
    contexto_final["tabla_configuracion"] = subdoc_config

    # Hook para renderizar los campos en formato imagen SOLO para Diccionario de Datos
    for idx, obj in enumerate(contexto_final.get("objetos_diccionario", [])):
        if obj.get("campos_crudos"):
            obj["campos_resumen"] = _construir_tabla_campos_subdoc(
                doc, obj["campos_crudos"], imagenes_tmp_dir, f"dicc_campos_{idx}"
            )
    # Variables que la plantilla realmente usa (detectadas por docxtpl al
    # parsear el .docx) vs las que le estamos mandando. Un nombre mal escrito
    # en el template o en document_builder aparece aquí ANTES de intentar
    # renderizar, sin necesitar reproducir el error a ciegas.
    try:
        esperadas = doc.get_undeclared_template_variables()
    except Exception:
        esperadas = None  # si esto falla, el propio render() dará el detalle real

    diagnostico_variables = ""
    if esperadas is not None:
        provistas = set(contexto_final.keys())
        faltantes = esperadas - provistas
        sobrantes = provistas - esperadas
        diagnostico_variables = (
            f"Variables que la plantilla espera: {sorted(esperadas)}\n"
            f"Variables provistas en el contexto: {sorted(provistas)}\n"
            f"Esperadas y NO provistas (romperán el render): {sorted(faltantes) or 'ninguna'}\n"
            f"Provistas y NO usadas por la plantilla (informativo): {sorted(sobrantes) or 'ninguna'}\n"
        )

    try:
        doc.render(contexto_final)  # ← ÚNICA llamada a render(), con todo adentro
        doc.save(output_path)
        return output_path
    except Exception:
        trace_completo = (
            f"--- TRACEBACK ---\n{traceback.format_exc()}\n\n"
            f"--- DIAGNÓSTICO DE VARIABLES ---\n{diagnostico_variables}\n"
            f"--- RESUMEN DEL CONTEXTO USADO ---\n{_resumen_contexto(contexto_final)}"
        )
        raise DocxBuildError(trace_completo)


def generar_documento_word(dtss_final: dict, metadata_json: dict, output_path: str,
                            cliente: str = "Cliente", proyecto: str = "Proyecto",
                            plantilla_path: str = None) -> str:
    """Arma el contexto y renderiza en un solo paso (conveniencia). Si
    necesitas loguear el contexto ANTES de intentar renderizar (recomendado),
    usa construir_contexto() + renderizar_documento() por separado."""
    contexto = construir_contexto(dtss_final, metadata_json, cliente=cliente, proyecto=proyecto)
    return renderizar_documento(contexto, output_path, plantilla_path=plantilla_path)
