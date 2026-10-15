# ruido_sap.py
#
# Catálogo de tablas técnicas conocidas de SAP que suelen aparecer como
# TABU (registros de tabla) en una Orden de Transporte SOLO como efecto
# colateral de generar/activar otro objeto (vista, tabla, programa, grupo
# de funciones, enhancement, etc.) — no porque el desarrollador haya
# querido documentar customizing de negocio ahí.
#
# Uso sugerido en el bot: si un objeto TABU tiene object_name en este set,
# NO se documenta como objeto de customizing propio; se omite del listado
# principal o se anexa como nota técnica del objeto que lo generó
# (ej. "TDDAT/TVDIR se regeneraron automáticamente al crear la vista X").
# Cualquier TABU cuyo nombre NO esté en este set (como ZTCONSTANTES) se
# trata como customizing real y se documenta con sus registros.

SAP_TECHNICAL_NOISE_TABLES = {
    # --- Generación de vistas / mantenimiento de tablas (SE11, SE54, SM30) ---
    "TDDAT",    # Ámbitos de actualización para tablas
    "TVDIR",    # Directorio de vistas
    "TVIMF",    # Asignación de grupo de funciones a vista de mantenimiento
    "TVIMC",    # Datos adicionales de la vista de mantenimiento
    "DD02L",    # Catálogo de tablas (cabecera técnica)
    "DD02T",    # Textos de tablas
    "DD03L",    # Campos de tabla/vista
    "DD03T",    # Textos de campos
    "DD04L",    # Elementos de datos
    "DD04T",    # Textos de elementos de datos
    "DD01L",    # Dominios
    "DD01T",    # Textos de dominios
    "DD07L",    # Valores fijos de dominio
    "DD07T",    # Textos de valores fijos de dominio
    "DD09L",    # Configuración técnica de tabla
    "DD12L",    # Índices de tabla
    "DD17S",    # Vistas: asignación de tablas base
    "DD25L",    # Vistas: cabecera de definición
    "DD26E",    # Vistas: componentes/campos
    "DD27S",    # Estructuras append
    "DD30L",    # Ayudas de búsqueda (cabecera técnica)
    "DD31L",    # Objetos de bloqueo (cabecera técnica)
    "DD31T",    # Textos de objetos de bloqueo

    # --- Generación de programas / includes / pantallas (SE38, SE80) ---
    "TRDIR",    # Directorio de programas ABAP (atributos técnicos)
    "REPOSRC",  # Almacenamiento físico del código fuente
    "D010INC",  # Relación de includes por programa
    "D020S",    # Directorio de pantallas (dynpros)
    "D021S",    # Campos de pantalla (dynpro)

    # --- Grupos de función / módulos de función (SE37, SE80) ---
    "TFDIR",    # Directorio de módulos de función
    "ENLFDIR",  # Atributos adicionales de módulos de función
    "TLIBG",    # Grupos de función
    "TLIBT",    # Textos de grupos de función
    "TFTIT",    # Textos cortos de módulos de función

    # --- Transacciones (se listan aparte como object_type TRAN; si
    #     aparecen también como TABU es redundante) ---
    "TSTC",     # Códigos de transacción
    "TSTCT",    # Textos de códigos de transacción

    # --- Enhancement Framework / BAdIs (efecto colateral de ENHO) ---
    "MODSAP",   # Asignación de mejoras SAP
    "MODSAPT",  # Textos de mejoras SAP
    "MODACT",   # Activación de mejoras/proyectos CMOD

    # --- Diccionario de mensajes (a veces se autogenera al activar un
    #     programa/clase que declara sus propias clases de mensaje) ---
    # "T100",   # Descomentar solo si en tu contexto nunca es customizing real
}

# Palabras clave adicionales para heurística de generación automática,
# útiles cuando el nombre de FUNC/FUGR no está en un set fijo (ej. los
# módulos de función generados por el Table Maintenance Generator o por
# la generación de vistas siempre siguen este patrón de prefijo):
AUTO_GENERATED_NAME_PREFIXES = (
    "VIEWFRAME_",   # Módulo de función nivel superior generado (Vistas de mant.)
    "VIEWPROC_",    # Módulo de función nivel inferior generado (Vistas de mant.)
    "SAPL",         # Programas generados de grupos de función
    "SAPM",         # Programas generados de vistas de mantenimiento
)


def is_technical_noise(object_type: str, object_name: str) -> bool:
    """
    True si el objeto (típicamente TABU, pero también aplica a FUNC/PROG
    autogenerados) es ruido técnico de SAP y no debería documentarse como
    customizing/lógica propia del desarrollo.
    """
    if object_type == "TABU":
        return object_name.upper() in SAP_TECHNICAL_NOISE_TABLES
    if object_type in ("FUNC", "PROG"):
        return object_name.upper().startswith(AUTO_GENERATED_NAME_PREFIXES)
    return False
