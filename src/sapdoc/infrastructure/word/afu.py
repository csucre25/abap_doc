# afu.py
#
# DTSS "completo": usa el AFU (.docx) que el usuario subió al bot como
# DOCUMENTO BASE y le agrega al final las secciones técnicas.
#
#   AFU original  →  portada, índice, control de versiones, secciones 1–4
#                    (documentación funcional tal cual, sin pasar por IA),
#                    encabezado con logo y pie de página
#   + generado    →  5. Desarrollo Técnico (5.1 lógica, 5.2 diccionario,
#                    5.3 configuración), 6. Órdenes de Transporte, 7. Anexos
#
# Es exactamente la estructura del DTSS manual de Repsol (el DTSS es el AFU
# + las secciones 5, 6 y 7), pero armada automáticamente.
#
# A diferencia de renderizar_documento() (docxtpl + plantilla_dtss.docx), aquí
# NO se usa Jinja: el AFU es un documento arbitrario del usuario y no se debe
# interpretar como plantilla. Las secciones nuevas se escriben directo con
# python-docx sobre el propio AFU, heredando sus estilos (Heading 1/2, fuentes,
# numeración automática de títulos, etc.).
#
# Requiere lo mismo que document_builder.py (python-docx, Pillow).

import os
import re
import tempfile
import traceback
from datetime import datetime

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph

from sapdoc.infrastructure.word.plantilla import (
    DocxBuildError,
    _agregar_tabla_generica,
    _construir_tabla_campos_subdoc,
    _generar_imagen_tabla,
    _reducir_columnas_constantes,
    _resumen_contexto,
)

# ---------------------------------------------------------------------------
# CONFIGURACIÓN (ajustable sin tocar la lógica)
# ---------------------------------------------------------------------------

# Colores: los mismos de plantilla_dtss.docx (build_template.py)
COLOR_BADGE = "1F4E78"      # fila de título del objeto / encabezado de tabla
COLOR_ETIQUETA = "D9E2F3"   # columna de etiquetas (Descripción, Explicación...)

ANCHO_ETIQUETA_CM = 4.2     # el ancho del valor se calcula con el ancho útil de página

# Títulos de las secciones nuevas. SIN número: el AFU numera sus títulos
# automáticamente (Heading 1/2 con numeración multinivel), así que "Desarrollo
# Técnico" sale como "5." y "Objetos con lógica de negocio" como "5.1." solos.
# Si el AFU no tuviera numeración automática, el número se antepone a mano.
TITULO_DESARROLLO = "Desarrollo Técnico"
TITULO_LOGICA = "Objetos con lógica de negocio"
TITULO_DICCIONARIO = "Objetos de diccionario"
TITULO_CONFIG = "Configuración"
TITULO_OT = "Órdenes de Transporte"
TITULO_ANEXOS = "Anexos"
TITULO_ACCIONES = "Acciones realizadas"

# Portada: el AFU dice "AFU"; el documento resultante es un DTSS.
# Se aplican SOLO a la portada (párrafos antes de "Contenido") y "AFU" → "DTSS"
# también en encabezado/pie. Cada entrada: (regex, reemplazo), en este orden.
REEMPLAZOS_PORTADA = [
    (r"^Plantilla:.*$", "Plantilla: 24 DTSS - Diseño de Implementación V1.0"),
    (r"^Análisis Funcional del Sistema$", "DTSS – Diseño de Implementación"),
    (r"\bAFU\b", "DTSS"),
]
REEMPLAZOS_ENCABEZADO_PIE = [(r"\bAFU\b", "DTSS")]

# El AFU de Repsol viene con protección "solo lectura" (solo editables los
# campos de la portada). El DTSS completo debe poder editarse.
QUITAR_PROTECCION = True

# Pide a Word que actualice el índice (TOC) y los números de página al abrir,
# para que aparezcan las secciones 5, 6 y 7 recién agregadas.
ACTUALIZAR_CAMPOS_AL_ABRIR = True


# ---------------------------------------------------------------------------
# UTILIDADES DE TEXTO
# ---------------------------------------------------------------------------
_RE_CARACTERES_ILEGALES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _limpiar(texto) -> str:
    """Deja el texto apto para XML de Word: sin caracteres de control (el
    código ABAP extraído a veces los trae y python-docx lanza ValueError) y
    con saltos de línea normalizados a \\n."""
    if texto is None:
        return ""
    texto = str(texto).replace("\r\n", "\n").replace("\r", "\n")
    return _RE_CARACTERES_ILEGALES.sub("", texto)


def _formatear_fecha_sap(valor) -> str:
    """'20260717' -> '17.07.2026'. Cualquier otro formato se devuelve igual."""
    valor = str(valor or "").strip()
    if re.fullmatch(r"\d{8}", valor):
        return f"{valor[6:8]}.{valor[4:6]}.{valor[0:4]}"
    return valor


def _reemplazar_en_parrafo(parrafo, regex, nuevo) -> bool:
    """Reemplaza texto que puede estar partido en varios runs (Word fragmenta
    el texto: 'AFU ' + 'Interfaz COW' + ...). El reemplazo va en el primer run
    afectado, conservando su formato; en los demás runs solo se recorta lo
    coincidente. Devuelve True si cambió algo."""
    cambiado = False
    inicio_busqueda = 0
    for _ in range(50):  # tope de seguridad contra bucles
        runs = parrafo.runs
        texto = "".join(r.text for r in runs)
        m = regex.search(texto, inicio_busqueda)
        if not m or m.end() == m.start():
            break
        ini, fin = m.span()
        pos = 0
        primero = True
        for r in runs:
            t = r.text
            r_ini, r_fin = pos, pos + len(t)
            pos = r_fin
            if r_fin <= ini or r_ini >= fin:
                continue
            a = max(ini, r_ini) - r_ini
            b = min(fin, r_fin) - r_ini
            if primero:
                r.text = t[:a] + nuevo + t[b:]
                primero = False
            else:
                r.text = t[:a] + t[b:]
        cambiado = True
        inicio_busqueda = ini + len(nuevo)
    return cambiado


# ---------------------------------------------------------------------------
# AJUSTES AL DOCUMENTO BASE (AFU)
# ---------------------------------------------------------------------------
def _adaptar_portada(doc, reemplazos=REEMPLAZOS_PORTADA):
    """AFU → DTSS en la portada. Se limita a los párrafos anteriores a
    'Contenido' para no tocar el cuerpo funcional del AFU."""
    compilados = [(re.compile(p), r) for p, r in reemplazos]
    for i, p in enumerate(doc.paragraphs):
        if p.text.strip() == "Contenido" or i > 80:
            break
        if not p.text.strip():
            continue
        for regex, nuevo in compilados:
            _reemplazar_en_parrafo(p, regex, nuevo)


def _adaptar_encabezado_pie(doc, reemplazos=REEMPLAZOS_ENCABEZADO_PIE):
    compilados = [(re.compile(p), r) for p, r in reemplazos]
    for sec in doc.sections:
        partes = [sec.header, sec.footer, sec.first_page_header, sec.first_page_footer,
                  sec.even_page_header, sec.even_page_footer]
        for parte in partes:
            # Si está "vinculado al anterior" no tiene definición propia: tocarlo
            # crearía un encabezado/pie vacío como efecto colateral.
            if parte.is_linked_to_previous:
                continue
            # iter() y no parte.paragraphs: el texto del pie del AFU vive dentro
            # de una tabla (Sistemas de Información | AFU ... | Pág. X de Y)
            for p_el in parte._element.iter(qn("w:p")):
                p = Paragraph(p_el, parte)
                for regex, nuevo in compilados:
                    _reemplazar_en_parrafo(p, regex, nuevo)


def _ajustar_settings(doc):
    settings = doc.settings.element
    if QUITAR_PROTECCION:
        for el in settings.findall(qn("w:documentProtection")):
            settings.remove(el)
    if ACTUALIZAR_CAMPOS_AL_ABRIR and settings.find(qn("w:updateFields")) is None:
        el = OxmlElement("w:updateFields")
        el.set(qn("w:val"), "true")
        # Posición según el esquema de CT_Settings: justo antes de estos elementos
        settings.insert_element_before(
            el, "w:hdrShapeDefaults", "w:footnotePr", "w:endnotePr", "w:compat",
            "w:docVars", "w:rsids", "m:mathPr", "w:attachedSchema", "w:themeFontLang",
            "w:clrSchemeMapping", "w:doNotIncludeSubdocsInStats",
            "w:doNotAutoCompressPictures", "w:forceUpgrade", "w:captions",
            "w:readModeInkLockDown", "w:smartTagType", "sl:schemaLibrary",
            "w:shapeDefaults", "w:doNotEmbedSmartTags", "w:decimalSymbol",
            "w:listSeparator",
        )


def _asegurar_table_grid(doc):
    """Garantiza que exista el estilo 'Table Grid' (bordes simples). Los AFU
    de Repsol lo traen; si algún AFU no, se crea para que ni las tablas
    nuevas ni el fallback de _agregar_tabla_generica fallen."""
    try:
        return doc.styles["Table Grid"]
    except KeyError:
        pass
    b = lambda lado: f'<w:{lado} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
    xml = (
        f'<w:style {nsdecls("w")} w:type="table" w:styleId="TableGrid">'
        '<w:name w:val="Table Grid"/><w:uiPriority w:val="39"/>'
        '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
        '<w:tblPr><w:tblBorders>'
        + "".join(b(l) for l in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + "</w:tblBorders></w:tblPr></w:style>"
    )
    doc.styles.element.append(parse_xml(xml))
    return doc.styles["Table Grid"]


# ---------------------------------------------------------------------------
# ADAPTADOR: celda de Word que "se ve" como el Subdoc de docxtpl
# ---------------------------------------------------------------------------
class _CeldaComoSubdoc:
    """_construir_tabla_campos_subdoc() (document_builder.py) escribe en un
    Subdoc de docxtpl usando solo add_paragraph / add_picture / add_table.
    Este adaptador expone esa misma interfaz sobre una celda de python-docx,
    para reutilizar tal cual la lógica que ya decide entre tabla DDIC,
    customizing o pares clave/valor y genera la imagen tipo SE11."""

    def __init__(self, celda):
        self._celda = celda
        self._primer_parrafo_libre = True

    def add_paragraph(self, texto=""):
        if self._primer_parrafo_libre:
            self._primer_parrafo_libre = False
            p = self._celda.paragraphs[0]
            if texto:
                p.add_run(_limpiar(texto))
            return p
        return self._celda.add_paragraph(_limpiar(texto))

    def add_picture(self, ruta, width=None):
        p = self.add_paragraph()
        return p.add_run().add_picture(ruta, width=width)

    def add_table(self, rows, cols):
        self._primer_parrafo_libre = False
        return self._celda.add_table(rows, cols)


class _FabricaSubdocCelda:
    """Lo que recibe _construir_tabla_campos_subdoc en lugar del 'doc' de docxtpl."""

    def __init__(self, celda):
        self._celda = celda

    def new_subdoc(self):
        return _CeldaComoSubdoc(self._celda)


# ---------------------------------------------------------------------------
# CONSTRUCTOR DE LAS SECCIONES TÉCNICAS
# ---------------------------------------------------------------------------
class _ConstructorTecnico:
    def __init__(self, doc, imagenes_tmp_dir):
        self.doc = doc
        self.tmp = imagenes_tmp_dir
        self.imagenes = []  # PNGs temporales a borrar al terminar

        sec = doc.sections[-1]
        ancho_util = sec.page_width - sec.left_margin - sec.right_margin
        self.ancho_util = ancho_util
        self.ancho_etiqueta = Cm(ANCHO_ETIQUETA_CM)
        self.ancho_valor = ancho_util - self.ancho_etiqueta
        self.ancho_util_cm = ancho_util / 360000  # EMU → cm

        _asegurar_table_grid(doc)

        # ¿Los títulos del AFU se numeran solos? Si no, se numeran a mano.
        self.auto_num = {n: self._titulo_numerado(n) for n in (1, 2)}
        self.h1 = sum(1 for p in doc.paragraphs if p.style is not None and p.style.name == "Heading 1")
        self.h2 = 0

    # -- títulos ------------------------------------------------------------
    def _titulo_numerado(self, nivel):
        try:
            pPr = self.doc.styles[f"Heading {nivel}"].element.pPr
        except KeyError:
            return True  # sin estilo no hay nada que numerar a mano
        return pPr is not None and pPr.numPr is not None

    def titulo(self, texto, nivel, salto_pagina=False):
        if nivel == 1:
            self.h1 += 1
            self.h2 = 0
        else:
            self.h2 += 1
        if not self.auto_num[nivel]:
            prefijo = f"{self.h1}." if nivel == 1 else f"{self.h1}.{self.h2}."
            texto = f"{prefijo} {texto}"
        try:
            p = self.doc.add_heading(texto, level=nivel)
        except KeyError:  # el AFU no trae Heading N: negrita como último recurso
            p = self.doc.add_paragraph()
            p.add_run(texto).bold = True
        if salto_pagina:
            p.paragraph_format.page_break_before = True
        if nivel == 1:
            self.doc.add_paragraph()  # línea en blanco tras cada título 1, como en el AFU
        return p

    def parrafo(self, texto="", negrita=False, cursiva=False):
        p = self.doc.add_paragraph()
        if texto:
            r = p.add_run(_limpiar(texto))
            r.bold = negrita
            r.italic = cursiva
        return p

    # -- tablas -------------------------------------------------------------
    def _nueva_tabla(self, filas, anchos):
        t = self.doc.add_table(rows=filas, cols=len(anchos))
        t.style = self.doc.styles["Table Grid"]
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        for i, w in enumerate(anchos):
            t.columns[i].width = w  # <w:gridCol> (lo respeta LibreOffice)
            for c in t.columns[i].cells:
                c.width = w         # <w:tcW> (lo respeta Word)
        return t

    @staticmethod
    def _sombrear(celda, color_hex):
        tcPr = celda._tc.get_or_add_tcPr()
        for viejo in tcPr.findall(qn("w:shd")):
            tcPr.remove(viejo)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), color_hex)
        tcPr.append(shd)

    @staticmethod
    def _escribir(celda, texto, negrita=False, color=None, size=None, mono=False, keep_next=False):
        p = celda.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        if keep_next:
            p.paragraph_format.keep_with_next = True
        run = p.add_run(_limpiar(texto))
        run.bold = negrita or None
        if mono:
            run.font.name = "Courier New"
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = RGBColor.from_string(color)
        return run

    def _tabla_objeto(self, etiqueta_tipo, nombre, filas):
        """Tabla de 2 columnas: fila-título (tipo | nombre) + una fila por cada
        (etiqueta, valor, opciones). 'valor' es str, o una función que recibe
        la celda y la llena (para campos/imágenes)."""
        t = self._nueva_tabla(1 + len(filas), [self.ancho_etiqueta, self.ancho_valor])
        c_tipo, c_nombre = t.rows[0].cells
        for celda, txt in ((c_tipo, etiqueta_tipo), (c_nombre, nombre)):
            self._sombrear(celda, COLOR_BADGE)
            self._escribir(celda, txt, negrita=True, color="FFFFFF", size=11, keep_next=True)
        for i, (etiqueta, valor, opts) in enumerate(filas, start=1):
            c_etq, c_val = t.rows[i].cells
            self._sombrear(c_etq, COLOR_ETIQUETA)
            self._escribir(c_etq, etiqueta, negrita=True)
            if callable(valor):
                valor(c_val)
            else:
                self._escribir(c_val, valor, mono=opts.get("mono", False), size=opts.get("size"))
        self.doc.add_paragraph()  # espacio entre objetos
        return t

    # -- secciones ----------------------------------------------------------
    def objetos_logica(self, objetos):
        if not objetos:
            self.parrafo("No se identificaron objetos con lógica de negocio en esta OT.")
        for o in objetos:
            self._tabla_objeto(o.get("object_type_label", ""), o.get("object_name", ""), [
                ("Descripción", o.get("descripcion", ""), {}),
                ("Explicación", o.get("explicacion", ""), {}),
                ("Código Fuente Nuevo:", o.get("codigo_nuevo", "N/A"), {"mono": True, "size": 8}),
                ("Código Fuente Anterior:", o.get("codigo_anterior", "N/A"), {"mono": True, "size": 8}),
            ])

    def objetos_diccionario(self, objetos):
        if not objetos:
            self.parrafo("No se identificaron objetos de diccionario en esta OT.")
        for idx, o in enumerate(objetos):
            crudos = o.get("campos_crudos")

            def llenar_campos(celda, crudos=crudos, idx=idx, o=o):
                if crudos:
                    prefijo = f"dtss_full_dicc_{os.getpid()}_{idx}"
                    self.imagenes.append(os.path.join(self.tmp, f"{prefijo}.png"))
                    _construir_tabla_campos_subdoc(_FabricaSubdocCelda(celda), crudos, self.tmp, prefijo)
                else:
                    self._escribir(celda, o.get("campos_resumen", "Sin campos registrados."))

            self._tabla_objeto(o.get("object_type_label", ""), o.get("object_name", ""), [
                ("Descripción", o.get("descripcion", ""), {}),
                ("Campos / Atributos", llenar_campos, {}),
            ])

    def configuracion(self, configuracion):
        if not configuracion:
            self.parrafo("No se identificaron objetos de customizing en esta OT.")
            return
        for idx, cfg in enumerate(configuracion):
            self.parrafo(f"{cfg.get('nombre', '')} — {cfg.get('descripcion', '')}", negrita=True)
            columnas, filas = cfg.get("columnas", []), cfg.get("filas", [])
            if not (columnas and filas):
                self.parrafo("Sin registros de customizing.")
                continue
            try:
                # Igual que el flujo docxtpl: imagen con TODAS las columnas
                # (parecida al ALV de SAP), a todo el ancho útil de la página.
                ruta = os.path.join(self.tmp, f"dtss_full_config_{os.getpid()}_{idx}.png")
                self.imagenes.append(ruta)
                _generar_imagen_tabla(columnas, filas, ruta)
                self.doc.add_paragraph().add_run().add_picture(ruta, width=Cm(self.ancho_util_cm))
            except Exception:
                # Sin Pillow: tabla nativa, con las columnas constantes resumidas en una nota
                cols_r, filas_r, nota = _reducir_columnas_constantes(columnas, filas)
                if nota:
                    self.parrafo(nota, cursiva=True)
                if cols_r:
                    _agregar_tabla_generica(self.doc, cols_r, filas_r)
            self.doc.add_paragraph()

    def ordenes_transporte(self, ordenes):
        if not ordenes:
            self.parrafo("No se registraron órdenes de transporte.")
            return
        # Orden | Usuario | Denominación | Fecha  (suman el ancho útil)
        u = self.ancho_util
        anchos = [int(u * 0.20), int(u * 0.17), int(u * 0.48), u - int(u * 0.20) - int(u * 0.17) - int(u * 0.48)]
        t = self._nueva_tabla(len(ordenes) + 1, anchos)
        for i, h in enumerate(["Orden", "Usuario", "Denominación", "Fecha"]):
            self._sombrear(t.rows[0].cells[i], COLOR_BADGE)
            self._escribir(t.rows[0].cells[i], h, negrita=True, color="FFFFFF")
        for r, ot in enumerate(ordenes, start=1):
            valores = [ot.get("orden", ""), ot.get("usuario", ""),
                       ot.get("descripcion", ""), _formatear_fecha_sap(ot.get("fecha", ""))]
            for c, v in enumerate(valores):
                self._escribir(t.rows[r].cells[c], v)
        self.doc.add_paragraph()

    def limpiar_temporales(self):
        for ruta in self.imagenes:
            try:
                if os.path.exists(ruta):
                    os.remove(ruta)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------
def renderizar_documento_con_afu(contexto: dict, afu_docx_path: str, output_path: str,
                                 imagenes_tmp_dir: str = None) -> str:
    """Toma el AFU (.docx) como base y le agrega las secciones técnicas.

    'contexto' es el que arma construir_contexto(..., escapar_xml=False):
    aquí NO se escapa nada, python-docx ya escapa por su cuenta (escapar
    también mostraría '&lt;' literal en el Word).

    Si falla, levanta DocxBuildError con traceback + resumen del contexto,
    igual que renderizar_documento(), para volcarlo tal cual a un log."""
    imagenes_tmp_dir = imagenes_tmp_dir or tempfile.gettempdir()
    constructor = None
    try:
        doc = Document(afu_docx_path)

        # 1) Ajustes al AFU: portada AFU→DTSS, sin protección, campos por actualizar
        _adaptar_portada(doc)
        _adaptar_encabezado_pie(doc)
        _ajustar_settings(doc)

        # 2) Secciones nuevas al final (cada título de nivel 1 en página nueva,
        #    como hace el propio AFU)
        constructor = _ConstructorTecnico(doc, imagenes_tmp_dir)

        constructor.titulo(TITULO_DESARROLLO, 1, salto_pagina=True)
        constructor.parrafo("A continuación se detallan todos los objetos ABAP que se crean o modifican en este desarrollo:")
        constructor.parrafo()

        constructor.titulo(TITULO_LOGICA, 2)
        constructor.objetos_logica(contexto.get("objetos_logica", []))

        constructor.titulo(TITULO_DICCIONARIO, 2)
        constructor.objetos_diccionario(contexto.get("objetos_diccionario", []))

        constructor.titulo(TITULO_CONFIG, 2)
        constructor.configuracion(contexto.get("configuracion", []))

        constructor.titulo(TITULO_OT, 1, salto_pagina=True)
        constructor.ordenes_transporte(contexto.get("ordenes_transporte", []))

        constructor.titulo(TITULO_ANEXOS, 1, salto_pagina=True)
        constructor.titulo(TITULO_ACCIONES, 2)
        constructor.parrafo("N/A")

        doc.save(output_path)
        return output_path
    except Exception:
        raise DocxBuildError(
            f"--- TRACEBACK (DTSS completo sobre AFU) ---\n{traceback.format_exc()}\n\n"
            f"--- AFU BASE ---\n{afu_docx_path}\n\n"
            f"--- RESUMEN DEL CONTEXTO USADO ---\n{_resumen_contexto(contexto)}"
        )
    finally:
        if constructor is not None:
            constructor.limpiar_temporales()
