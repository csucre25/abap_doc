from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def set_cell_shading(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    tcPr.append(shd)

def set_cell_text(cell, text, bold=False, color=None, size=None, mono=False, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    if align:
        p.alignment = align
    run = p.add_run(text)
    run.bold = bold
    if mono:
        run.font.name = "Courier New"
    if size:
        run.font.size = Pt(size)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    return run

def add_object_table(doc, badge_bg="1F4E78"):
    """Tabla de 5 filas x 2 columnas para un objeto con lógica."""
    table = doc.add_table(rows=5, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = [Cm(4), Cm(11.8)]
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = widths[i]

    # Fila 1: [Tipo Objeto] | [{{ objeto.object_name }}]
    r0 = table.rows[0]
    set_cell_shading(r0.cells[0], badge_bg)
    set_cell_text(r0.cells[0], "{{ objeto.object_type_label }}", bold=True, color="FFFFFF", size=11)
    set_cell_shading(r0.cells[1], badge_bg)
    set_cell_text(r0.cells[1], "{{ objeto.object_name }}", bold=True, color="FFFFFF", size=11)

    # Fila 2: Descripción
    set_cell_shading(table.rows[1].cells[0], "D9E2F3")
    set_cell_text(table.rows[1].cells[0], "Descripción", bold=True)
    set_cell_text(table.rows[1].cells[1], "{{ objeto.descripcion }}")

    # Fila 3: Explicación
    set_cell_shading(table.rows[2].cells[0], "D9E2F3")
    set_cell_text(table.rows[2].cells[0], "Explicación", bold=True)
    set_cell_text(table.rows[2].cells[1], "{{ objeto.explicacion }}")

    # Fila 4: Campos / Estructura (tabla real vía Subdoc, armada en document_builder.py)
    #set_cell_shading(table.rows[3].cells[0], "D9E2F3")
    #set_cell_text(table.rows[3].cells[0], "Campos / Estructura", bold=True)
    #set_cell_text(table.rows[3].cells[1], "{{ objeto.campos_resumen }}")

    # Fila 5: Código Fuente Añadido (código completo si es objeto nuevo,
    # o solo el diff si es una modificación — lo decide bot.py)
    set_cell_shading(table.rows[3].cells[0], "D9E2F3")
    set_cell_text(table.rows[3].cells[0], "Código Fuente Nuevo:", bold=True)
    set_cell_text(table.rows[3].cells[1], "{{ objeto.codigo_nuevo }}", mono=True, size=9)

    set_cell_shading(table.rows[4].cells[0], "D9E2F3")
    set_cell_text(table.rows[4].cells[0], "Código Fuente Anterior:", bold=True)
    set_cell_text(table.rows[4].cells[1], "{{ objeto.codigo_anterior }}", mono=True, size=9)

    return table

def add_dict_object_table(doc, badge_bg="1F4E78"):
    """Tabla más liviana para objetos de diccionario (tablas, DTEL, vistas, ayudas de búsqueda,
    customizing real): sin 'Explicación' redactada por IA, solo lo que ya viene de SAP."""
    table = doc.add_table(rows=3, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = [Cm(4.2), Cm(11.8)]
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = widths[i]

    r0 = table.rows[0]
    set_cell_shading(r0.cells[0], badge_bg)
    set_cell_text(r0.cells[0], "{{ objeto.object_type_label }}", bold=True, color="FFFFFF", size=11)
    set_cell_shading(r0.cells[1], badge_bg)
    set_cell_text(r0.cells[1], "{{ objeto.object_name }}", bold=True, color="FFFFFF", size=11)

    set_cell_shading(table.rows[1].cells[0], "D9E2F3")
    set_cell_text(table.rows[1].cells[0], "Descripción", bold=True)
    set_cell_text(table.rows[1].cells[1], "{{ objeto.descripcion }}")

    set_cell_shading(table.rows[2].cells[0], "D9E2F3")
    set_cell_text(table.rows[2].cells[0], "Campos / Atributos", bold=True)
    set_cell_text(table.rows[2].cells[1], "{{ objeto.campos_resumen }}")

    return table

def add_tag_paragraph(doc, text):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.italic = True
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string("808080")
    return p

doc = Document()

# --- Portada simple ---
title = doc.add_heading("{{ cliente }} – {{ proyecto }}", level=0)
p = doc.add_paragraph()
p.add_run("Documentación Técnica generada automáticamente").italic = True
doc.add_paragraph("Versión: {{ version }}")
doc.add_paragraph("Fecha: {{ fecha }}")
doc.add_page_break()

# --- Sección 5. Desarrollo Técnico ---
doc.add_heading("5. Desarrollo Técnico", level=1)

doc.add_heading("5.1. Objetos con lógica de negocio", level=2)
add_tag_paragraph(doc, "{%p for objeto in objetos_logica %}")
add_object_table(doc)
doc.add_paragraph()  # espacio entre objetos
add_tag_paragraph(doc, "{%p endfor %}")

doc.add_heading("5.2. Objetos de diccionario (tal cual, sin redacción de IA)", level=2)
add_tag_paragraph(doc, "{%p for objeto in objetos_diccionario %}")
add_dict_object_table(doc)
doc.add_paragraph()
add_tag_paragraph(doc, "{%p endfor %}")

# --- Sección Configuración (customizing real, ej. ZTCONSTANTES) ---
doc.add_heading("5.3. Configuración", level=2)
# Tabla(s) armadas como Subdoc en document_builder.py — mismo mecanismo que
# tabla_ots: una sola instancia armada en Python, con columnas dinámicas
# según lo que traiga cada objeto de customizing (no un Subdoc por objeto
# dentro de un loop de Jinja).
p_config = doc.add_paragraph()
p_config.add_run("{{ tabla_configuracion }}")

# --- Sección Constantes ---
#doc.add_heading("5.4. Declaración de constantes", level=2)
#add_tag_paragraph(doc, "{%p for c in constantes %}")
#p = doc.add_paragraph()
#p.add_run("{{ c.nombre }}").bold = True
#p.add_run(" = {{ c.valor }} — {{ c.descripcion }}")
#add_tag_paragraph(doc, "{%p endfor %}")

# --- Sección Órdenes de Transporte ---
doc.add_heading("6. Órdenes de Transporte", level=1)
# La tabla se arma como Subdoc en document_builder.py (tabla nativa de
# python-docx) y se inyecta aquí como un único placeholder. No usar un loop
# {%tr for %}/{%tr endfor %} aquí si document_builder ya arma un Subdoc:
# solo uno de los dos mecanismos debe estar activo a la vez.
p_tabla = doc.add_paragraph()
p_tabla.add_run("{{ tabla_ots }}")

# Ejecutar desde la raíz del proyecto: python scripts/build_template.py
doc.save("plantillas/plantilla_dtss.docx")
print("Plantilla creada.")