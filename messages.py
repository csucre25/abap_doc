# messages.py

TEXTS = {
    "es": {
        # ==========================================
        # 1. TEXTOS DE INTERFAZ (FLUJO CONVERSACIONAL TELEGRAM)
        # ==========================================
        "start_v2": (
            "¡Hola! Soy Chitra, tu Arquitecta de Soluciones SAP automatizada. 🚀\n\n"
            "Comencemos con la <b>Fase 0</b>: Envíame un documento histórico (.docx o .pdf) que "
            "cumpla con los estándares de tu cliente actual para calibrar mi estilo de redacción.\n\n"
            "Si no tienes uno, presiona 'Saltar'."
        ),
        "btn_skip_calib": "⏭️ Saltar Calibración",

        "msg_calib_analyzing": "📥 Guardando documento de calibración...",
        "msg_calib_success": "✅ Calibración en cola de procesamiento.\n\n<b>Fase 1:</b> Ahora envíame el Documento Funcional (AFU/ARN).",
        "msg_calib_skipped": "⏭️ Fase 0 omitida. Usaré el tono corporativo por defecto.\n\n<b>Fase 1:</b> Envíame el Documento Funcional (AFU/ARN).",

        "msg_afu_analyzing": "📥 Guardando documento funcional...",
        "msg_afu_success_v2": "✅ Requerimientos en cola de procesamiento.\n\n<b>Fases Técnicas:</b> Sube el archivo `.zip` de la OT generado desde SAP.",

        "msg_zip_downloading_v2": "📥 Extrayendo metadata y código fuente ABAP...",
        "msg_zip_ask_cr": (
            "📦 Metadata y código extraídos correctamente.\n\n"
            "¿Deseas que audite el código (Principios Clean Core / Mejores prácticas) "
            "antes de generar la documentación técnica?"
        ),
        "btn_run_cr": "🕵️‍♀️ Ejecutar Code Review",
        "btn_skip_cr": "🚀 Documentar directo: solo parte técnica",
        "btn_skip_cr_full": "📚 Documentar directo: técnica + requisitos funcionales",

        "msg_cr_analyzing": "🕵️‍♀️ Fase 1.5: Realizando Code Review y auditoría ABAP...",
        "msg_cr_violations": "⚠️ <b>Atención:</b> He detectado deuda técnica en la Orden de Transporte:\n\n{}\n\n¿Qué deseas hacer?",
        "btn_ignore_cr": "✅ Ignorar y Generar",
        "btn_cancel_cr": "🛑 Cancelar (Corregir en SAP)",
        "msg_cr_clean": "✅ Código impecable. Iniciando Fases 2, 3 y 4...",
        "msg_cr_skipped": "⏭️ Code Review omitido. Iniciando Fases 2, 3 y 4 (Mapeo y Redacción)...",

        "msg_process_cancelled": "🛑 Proceso cancelado. Corrige los hallazgos en SAP, genera un nuevo ZIP y envíamelo de nuevo.",
        "msg_global_cancel": "🛑 Operación abortada. Escribe /start para comenzar de nuevo.",
        "msg_pipeline_success": "🎉 <b>Documentación finalizada:</b>\n\n{}",

        # ==========================================
        # 2. VALIDACIONES Y ERRORES DE USUARIO
        # ==========================================
        "msg_invalid_word": "⚠️ Por favor, envíame un archivo Word o PDF válido.",
        "msg_invalid_zip": "⚠️ Por favor, envíame el archivo ZIP de la OT válido.",
        "afu_error": "⚠️ Ocurrió un error al leer el archivo. Verifica que no esté dañado o protegido y vuelve a intentarlo.",
        "zip_error": "❌ Ocurrió un error al procesar el archivo o consultar a la IA. Revisa la consola.",
        "zip_missing_meta": "❌ Error: El archivo ZIP no contiene un 'metadata.json'.",

        # ==========================================
        # 3. ARCHIVOS ADJUNTOS DE SALIDA
        # ==========================================
        "msg_log_caption": "📜 Log detallado de todos los prompts y respuestas para auditoría de IA.",
        "msg_draw_doc": "📄 Documento Técnico (Word) generado con la plantilla DTSS.",
        "msg_json_backup": "🗂️ JSON de respaldo (estructura completa para auditoría o reproceso manual).",
        "msg_csv_config": "📊 CSV de customizing (datos completos sin compactar, para validar o recargar la configuración).",
        "msg_no_autorizado": (
            "⛔ No estás autorizado para usar este bot.\n"
            "Tu ID de Telegram es {user_id}. Compártelo con el administrador si necesitas acceso."
        ),
        "aviso_calib_sin_texto": (
            "No se pudo usar el documento de calibración: no contiene texto extraíble "
            "(¿es un PDF escaneado?). Se aplicó el estilo por defecto."
        ),
        "aviso_calib_fallida": (
            "No se pudo usar el documento de calibración: la IA no devolvió un resultado válido. "
            "Se aplicó el estilo por defecto."
        ),
        "msg_critical_error":"Error crítico al enviar archivos adjuntos: {log_e}",
        "msg_full_dtss_ok": "🎉 <b>DTSS completo generado.</b>\n\nAdjunto el Word con los requisitos funcionales del AFU y el desarrollo técnico.",
        "msg_full_dtss_caption": (
            "📄 DTSS completo: requisitos funcionales (AFU) + desarrollo técnico.\n"
            "Al abrirlo en Word, acepta actualizar los campos para que el índice incluya las secciones 5, 6 y 7."
        ),

        # ==========================================
        # 4. LOGS INTERNOS (CONSOLA)
        # ==========================================
        "log_missing_creds": "¡Faltan credenciales en el archivo .env (TELEGRAM_BOT_TOKEN o NVIDIA_API_KEY)!",
        "log_bot_listening": "Chitra SAP Bot escuchando en Telegram (Long-polling)...",
        "log_error_afu": "Error procesando el documento funcional: {}",
        "log_error_zip": "Error procesando el documento técnico: {}",

        # ==========================================
        # 5. FRASES DE CARGA DINÁMICAS Y TEXTOS DE PROGRESO POR FASE
        # ==========================================
        "loading_phrases_fase0": [
            "Extrayendo estándares de redacción corporativa",
            "Analizando la estructura y el uso de viñetas",
            "Identificando el tono y la verbosidad del documento",
            "Calibrando el modelo de lenguaje al estilo del cliente"
        ],
        "loading_phrases_fase1": [
            "Leyendo el documento de diseño funcional",
            "Identificando reglas de negocio y restricciones",
            "Estructurando los criterios de aceptación",
            "Clasificando los requerimientos atómicos (REQ)"
        ],
        "loading_phrases_tech": [
            "Revisando las tablas del diccionario de datos",
            "Compilando los Includes y Módulos de Funciones",
            "Consultando las BAPIs y RFCs disponibles",
            "Validando la matriz de compatibilidad S/4HANA",
            "Analizando la arquitectura Clean Core",
            "Mapeando requerimientos contra objetos Z",
            "Calculando la complejidad ciclomática del ABAP"
        ],
        "zip_fase_paralela": "⚙️ <b>Fases 0, 1 y 2 en Paralelo:</b> Analizando Calibración, AFU y Código ABAP simultáneamente...",
        "zip_fase4": "📝 Redactando justificación de negocio...",

        # ==========================================
        # 6. CALIBRACIÓN POR DEFECTO (CUANDO SE OMITE LA FASE 0)
        # ==========================================
        "default_calibration": {
            "tone": "Formal, corporativo, analítico y técnico",
            "verbosity": "Alto nivel de detalle, descriptivo y estructurado",
            "formatting_rules": [
                "Numeración jerárquica estricta de secciones (ej. 1., 1.1., 1.1.1)",
                "Uso exhaustivo de tablas formales para diccionarios de datos y constantes",
                "Encabezados de sección resaltados en negrita",
                "Listas con viñetas para enumerar lógicas de negocio o validaciones",
                "Redacción en tercera persona orientada a justificar la viabilidad técnica de la solución"
            ],
            "boilerplate_phrases": [
                "El presente documento técnico detalla...",
                "La arquitectura técnica implementada consiste en...",
                "El objetivo de este desarrollo es garantizar la correcta integración...",
                "Las especificaciones técnicas y dependencias son las siguientes:"
            ],
            "clean_core_strictness": "Alta"
        },

        # ==========================================
        # 7. FASE 0: EL AGENTE CALIBRADOR (QA DE FORMATO)
        # ==========================================
        "sys_prompt_fase0": (
            "Eres un Agente de Calibración de Calidad (QA) y Experto en Documentación SAP. "
            "Tu objetivo es analizar un documento histórico de Diseño Técnico provisto por el cliente "
            "y extraer sus estándares de redacción para clonar su estilo.\n\n"
            "FORMATO DE SALIDA (JSON PURO):\n"
            "Devuelve ÚNICA Y EXCLUSIVAMENTE un objeto JSON válido. NO incluyas texto adicional ni bloques markdown.\n\n"
            "ESTRUCTURA EXACTA DEL JSON:\n"
            "{\n"
            "  \"tone\": \"Descripción del tono (ej. Formal, Corporativo)\",\n"
            "  \"verbosity\": \"Nivel de detalle\",\n"
            "  \"formatting_rules\": [\"Regla 1\", \"Regla 2\"],\n"
            "  \"boilerplate_phrases\": [\"Frase repetitiva 1\", \"Frase 2\"],\n"
            "  \"clean_core_strictness\": \"Alta o Baja\"\n"
            "}"
        ),
        "user_prompt_fase0": (
            "Extrae las reglas de calibración de este documento histórico respetando estrictamente el formato JSON:\n\n"
            "<document>\n{texto_historico}\n</document>"
        ),

        # ==========================================
        # 8. FASE 1: EL ANALISTA FUNCIONAL
        # ==========================================
        "sys_prompt_fase1": (
            "Eres un Analista Funcional SAP Senior y Arquitecto de Requerimientos. Tu objetivo es leer texto "
            "extraído de un Documento de Análisis Funcional (AFU) o Requerimientos de Negocio (ARN) y extraer "
            "las necesidades atómicas del negocio.\n\n"
            "REGLAS CRÍTICAS E INQUEBRANTABLES:\n"
            "1. ANTI-ALUCINACIÓN: Prohibido inventar requerimientos, transacciones, tablas o procesos que no estén explícitos.\n"
            "2. MANEJO DE AMBIGÜEDAD: Si detectas un requerimiento a medias, inclúyelo pero asígnale status 'ambiguo' "
            "y explica por qué en 'omission_reason'. Si es completo, status 'activo'.\n"
            "3. FORMATO DE ID: Usa una secuencia estricta de 3 dígitos (REQ-001, REQ-002).\n"
            "4. El proposito de este analisis es dar contexto a una solucion construida en ABAP para que pueda ser documentada .\n\n"
            "FORMATO DE SALIDA (JSON PURO):\n"
            "Devuelve ÚNICA Y EXCLUSIVAMENTE un objeto JSON válido. NO incluyas texto adicional ni bloques markdown.\n\n"
            "ESTRUCTURA EXACTA DEL JSON:\n"
            "{\n"
            "  \"document_type\": \"AFU o ARN\",\n"
            "  \"general_objective\": \"Resumen de 2 líneas del objetivo principal\",\n"
            "  \"requirements\": [\n"
            "    {\n"
            "      \"req_id\": \"REQ-001\",\n"
            "      \"title\": \"Nombre corto\",\n"
            "      \"description\": \"Descripción funcional detallada\",\n" 
            "      \"status\": \"activo o omitido\",\n"
            "      \"omission_reason\": \"Motivo de AMBIGÜEDAD o null\"\n"
            "    }\n"
            "  ]\n"
            "}"
        ),
        "user_prompt_fase1": (
            "Extrae los requerimientos del siguiente documento respetando estrictamente el formato JSON:\n\n"
            "<documento_funcional>\n{texto_del_documento}\n</documento_funcional>"
        ),

        # ==========================================
        # 9. FASE 1.5: EL AUDITOR CLEAN CORE (CODE REVIEW)
        # ==========================================
        "sys_prompt_fase1_5": (
            "Eres un Auditor de Código SAP y Experto en Arquitectura Clean Core. Analiza la metadata técnica "
            "(JSON) y el código ABAP de una Orden de Transporte.\n\n"
            "REGLAS DE AUDITORÍA:\n"
            "Revisa el campo 's4core' en 'system_info' de la metadata.\n"
            "1. Si existe (S/4HANA): Sé estricto sancionando el uso de SELECT *, SELECT en LOOPs, BAPIs no liberadas, o falta de CDS/AMDP.\n"
            "2. Si es ECC (s4core vacío): Evalúa solo buenas prácticas estándar.\n\n"
            "FORMATO DE SALIDA (JSON PURO):\n"
            "Devuelve ÚNICA Y EXCLUSIVAMENTE un objeto JSON válido. NO incluyas bloques markdown.\n\n"
            "ESTRUCTURA EXACTA DEL JSON:\n"
            "{\n"
            "  \"has_violations\": true,\n"
            "  \"findings\": [\n"
            "    {\n"
            "      \"object_name\": \"Nombre del objeto afectado\",\n"
            "      \"violation_type\": \"Performance / Clean Core / Sintaxis / Seguridad\",\n"
            "      \"description\": \"Hallazgo detallado\",\n"
            "      \"recommendation\": \"Recomendación técnica\"\n"
            "    }\n"
            "  ]\n"
            "}"
        ),
        "user_prompt_fase1_5": (
            "Realiza el Code Review de esta OT devolviendo únicamente el JSON solicitado:\n\n"
            "<metadata>\n{metadata_json}\n</metadata>\n\n<abap>\n{codigo_abap}\n</abap>"
        ),

        # ==========================================
        # 10. FASE 2: EL REVISOR DE CÓDIGO ABAP
        # ==========================================
        "sys_prompt_fase2": (
            "Eres un Consultor SAP ABAP Senior experto en ingeniería inversa y documentación técnica. "
            "Tu tarea es analizar el metadato y el código fuente físico de una Orden de Transporte SAP.\n\n"
            "OBJETIVO: Documentar de forma estructurada para reemplazar capturas de pantalla (SE11/SE24/SE38) "
            "por datos técnicos crudos que luego se renderizarán en tablas. El código fuente completo se "
            "toma directo del archivo original (no lo transcribas tú); enfócate en extraer TODOS los "
            "campos DDIC relevantes con su tipo, longitud y elemento de datos (rollname).\n\n"
            "IDIOMA: Todo el texto libre (resúmenes, descripciones) debe redactarse en ESPAÑOL. Las palabras "
            "reservadas de ABAP (SELECT, LOOP, ENDLOOP, DATA, TYPES, PERFORM, METHOD, CLASS, IF, CASE, etc.), "
            "así como nombres de tablas, campos, variables, clases y demás identificadores técnicos, se "
            "mantienen tal cual aparecen en el código (en inglés), sin traducir. No mezcles idiomas dentro de "
            "una misma oración más allá de esos identificadores técnicos.\n\n"
            "FORMATO DE SALIDA (JSON PURO):\n"
            "Devuelve ÚNICA Y EXCLUSIVAMENTE un ARRAY JSON válido. NO incluyas texto adicional ni bloques markdown.\n\n"
            "ESTRUCTURA EXACTA DEL ARRAY JSON:\n"
            "[\n"
            "  {\n"
            "    \"object_name\": \"Nombre del objeto (ej. ZTPM_LABWARE)\",\n"
            "    \"object_type\": \"TABL, STRU, PROG, CLAS, ENHO, etc.\",\n"
            "    \"hierarchy_level\": 1,\n"
            "    \"executive_summary\": \"Resumen conciso (2 líneas) de lo que logra el objeto.\",\n"
            "    \"business_mapping_anchors\": {\n"
            "      \"functional_domain\": [\"PM\", \"Mantenimiento\"],\n"
            "      \"operation_type\": \"Tabla / Interfaz\"\n"
            "    },\n"
            "    \"technical_mechanics\": {\n"
            "      \"description\": \"Explicación técnica detallada de la lógica o propósito del objeto.\",\n"
            "      \"ddic_fields\": [\n"
            "        {\"field_name\": \"ZCAMPO\", \"type\": \"CHAR\", \"length\": 10, \"rollname\": \"ZDE_CAMPO\", \"description\": \"Descripción del campo\"}\n"
            "      ]\n"
            "    },\n"
            "    \"data_flow\": {\n"
            "      \"tables_read\": [\"EQUI\"],\n"
            "      \"tables_updated_or_inserted\": [\"ZTPM_LABWARE\"]\n"
            "    },\n"
            "    \"objects_called\": [\"ZCLWS_INTERFAZ_LABWARE\"]\n"
            "  }\n"
            "]"
        ),
        "user_prompt_fase2": (
            "Documenta la siguiente información de la Orden de Transporte SAP. Prioriza los "
            "Reportes/Programas al inicio del JSON y sigue estrictamente la estructura solicitada:\n\n"
            "<transport_metadata>\n{metadata_json}\n</transport_metadata>\n\n<abap_source_code>\n{codigo_abap}\n</abap_source_code>"
        ),

        # ==========================================
        # 11. FASE 4: EL TRADUCTOR CORPORATIVO (GENERACIÓN DE TEXTOS JSON)
        # ==========================================
        # NOTA: la Fase 3 (mapeo requisito↔objeto) se eliminó. El AFU ya no se
        # usa para amarrar cada objeto a un req_id específico (ese cruce era
        # poco confiable); ahora se pasa como CONTEXTO FUNCIONAL GENERAL del
        # proyecto para que la redacción de 'justificacion' tenga sustento de
        # negocio sin depender de un mapeo objeto-por-objeto.
        "sys_prompt_fase4": (
            "Actúa como un Arquitecto de Soluciones SAP Senior. Recibirás el CONTEXTO FUNCIONAL GENERAL de un "
            "proyecto (objetivo general y requerimientos de negocio del AFU) y un JSON con los objetos ABAP "
            "técnicos ya analizados. Tu único objetivo es redactar textos corporativos. "
            "NO generes Markdown, devuelve ÚNICAMENTE un objeto JSON.\n\n"
            "IDIOMA: Todo el texto debe redactarse en ESPAÑOL. Las palabras reservadas de ABAP, nombres de "
            "tablas, campos, variables, clases y demás identificadores técnicos se mantienen tal cual (en "
            "inglés), sin traducir. No mezcles idiomas dentro de una misma oración más allá de esos "
            "identificadores técnicos.\n\n"
            "REGLAS DE REDACCIÓN:\n"
            "1. 'justificacion': Explica el valor de negocio de este objeto apoyándote en el contexto "
            "funcional general del proyecto (objetivo general y requerimientos activos). No es necesario ni "
            "obligatorio vincular el objeto a un requisito específico por su ID; basta con que la "
            "justificación sea coherente con el propósito general del desarrollo. Si el objeto claramente no "
            "se relaciona con ningún requerimiento descrito, dilo de forma directa sin inventar una relación.\n"
            "2. 'logica_tecnica': Traduce la 'descripcion_cruda' a un resumen ejecutivo y fácil de leer para "
            "un líder funcional.\n\n"
            "ESTRUCTURA DE SALIDA (JSON PURO):\n"
            "{\n"
            "  \"redacciones\": [\n"
            "    {\n"
            "      \"nombre\": \"NOMBRE_DEL_OBJETO\",\n"
            "      \"justificacion\": \"Texto corporativo...\",\n"
            "      \"logica_tecnica\": \"Texto ejecutivo...\"\n"
            "    }\n"
            "  ]\n"
            "}"
        ),
        "user_prompt_fase4": (
            "Contexto funcional general del proyecto (usar solo como trasfondo, no como mapeo obligatorio):\n"
            "<contexto_funcional>\n{contexto_funcional}\n</contexto_funcional>\n\n"
            "Redacta los campos solicitados para los siguientes objetos y devuelve estrictamente el formato JSON:\n\n"
            "{json_objetos}"
        ),
    }
}