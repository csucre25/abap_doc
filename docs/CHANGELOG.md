# Changelog

## 2026-10-15

### env.example con un bloque completo por proveedor de IA
- Variables comunes (canal, auditoría, documento) separadas de las del
  proveedor de IA.
- Un bloque autocontenido por proveedor (NVIDIA, OpenAI directo, Azure
  OpenAI) con todas sus variables, incluidos los modelos por fase, para
  cambiar de proveedor comentando un bloque y descomentando otro.
- Las explicaciones dentro de los bloques usan `##`, de modo que comentar o
  descomentar el bloque entero con Ctrl+/ no las convierte en variables.
- Test `tests/test_env_example.py`: cada bloque activado solo da una
  configuración válida, el ejemplo se versiona con un único bloque activo y
  no hay variables de IA fuera de los bloques.

### CLAUDE.md actualizado
- Refleja el proyecto actual (python-telegram-bot, capas en `src/sapdoc`,
  proveedores de IA) en lugar de aiogram y el panel web, y recoge comandos,
  reglas de cambio, convenciones de tests, datos sensibles y pendientes.

### Documentación del código y del flujo de ejecución
- Todas las funciones y clases tienen docstring que explica qué hacen y en
  qué punto del flujo intervienen; los módulos `bot.py` y `textos.py` tienen
  descripción de cabecera. Solo se añadió texto, sin cambios de código.
- Nuevo `README.md`: estructura, instalación, configuración, ejecución, flujo
  de ejecución paso a paso (arranque, control de acceso, conversación y
  generación del documento) y los dos registros de auditoría.
- `tests/test_arquitectura.py` falla si una función o clase nueva no tiene
  docstring.

### Estructura del proyecto por capas (`src/sapdoc`)
- El código pasa a un paquete instalable con cuatro capas:
  - `domain/`: entidades y reglas puras (clasificación de objetos, catálogo de
    ruido SAP, reparación del JSON de la IA).
  - `application/`: casos de uso (documentar una OT, Code Review, análisis de
    calibración y AFU), textos y prompts, y los puertos `ClienteIA` y
    `GeneradorDocumento`.
  - `infrastructure/`: configuración, cliente de IA, lectura de PDF/Word y ZIP,
    generación del Word y registro de auditoría.
  - `interfaces/telegram/`: el bot.
- El pipeline ya no importa el generador de Word: lo recibe inyectado
  (`GeneradorWord`), igual que el cliente de IA. `tests/test_arquitectura.py`
  falla si una capa importa a otra más externa o si el núcleo usa bibliotecas
  de E/S.
- `pyproject.toml` reemplaza a `requirements.txt` y `requirements-dev.txt`;
  `requirements.lock` se mantiene para reproducir el entorno exacto.
- Comandos nuevos tras `pip install -e .`: `python -m sapdoc` o
  `sapdoc-telegram` (arranca el bot) y `sapdoc-verificar-auditoria <ruta>`.
- La plantilla pasa a `plantillas/plantilla_dtss.docx` y el generador de la
  plantilla a `scripts/build_template.py`.
- Sin cambios de comportamiento.

## 2026-10-14

### Registro de auditoría permanente
- Nuevo `bitacora.py`: registro de eventos en JSONL (por defecto
  `auditoria/eventos.jsonl`, configurable con `AUDITORIA_PATH`, fuera de git).
  Solo se añade al final y cada línea lleva el SHA-256 de la anterior;
  `python bitacora.py verificar <ruta>` detecta líneas modificadas, borradas
  o reordenadas.
- Eventos: `acceso_denegado` (usuario no autorizado o chat no privado),
  `ejecucion_iniciada` (usuario con nombre y rol, OT, nombre/tamaño/SHA-256 de
  calibración, AFU y ZIP, modo, estado del Code Review, proveedor y modelos de
  IA), `ejecucion_finalizada` (estado, SHA-256 del Word, JSON y CSV, avisos,
  error, duración), `code_review_cancelado` y `envio_fallido`.
- El Code Review queda registrado como `omitido`, `sin_hallazgos`,
  `con_hallazgos`, `hallazgos_ignorados` o `ilegible`. Para eso
  `revisar_codigo` marca ahora con `"ilegible": true` la respuesta que no pudo
  leer (el flujo no cambia: sigue sin bloquear).
- No se guarda contenido de documentos, solo metadatos y huellas.
- Limitación: la cadena detecta alteraciones, pero quien pueda escribir el
  archivo podría regenerarla entera; para evitarlo hace falta enviar los
  eventos a un almacenamiento externo de solo-añadir.
- Tests `tests/test_bitacora.py` y nuevos casos en `tests/test_bot_acceso.py`.

### Usuarios autorizados en un archivo JSON con nombre y rol
- La lista blanca pasa de `TELEGRAM_USUARIOS_PERMITIDOS` (IDs en el `.env`) a
  un archivo JSON con un objeto por persona: `telegram_id`, `nombre` y `rol`
  (`administrador` o `usuario`). Ruta configurable con
  `USUARIOS_PERMITIDOS_PATH` (por defecto `usuarios_permitidos.json`).
- El archivo real queda fuera de git por contener datos personales; se
  versiona `usuarios_permitidos.example.json` como plantilla.
- Sin archivo o con el array vacío nadie puede usar el bot. Un archivo mal
  formado (JSON inválido, ID entre comillas o repetido, rol desconocido)
  impide arrancar y dice qué falla. Si el `.env` aún trae
  `TELEGRAM_USUARIOS_PERMITIDOS`, también impide arrancar e indica cómo migrar.
- Los roles se guardan pero todavía no restringen nada.
- Tests en `tests/test_config.py` y `tests/test_bot_acceso.py`.

### Lista blanca de usuarios y solo chats privados
- Nueva variable `TELEGRAM_USUARIOS_PERMITIDOS` (IDs numéricos separados por
  comas). Un control previo a todos los handlers corta cualquier mensaje o
  botón de un usuario que no esté en la lista, antes de descargar archivos o
  llamar a la IA, y lo registra en consola.
- Al usuario no autorizado se le responde con su ID, para que pueda pedir
  acceso. Si la lista está vacía nadie puede usar el bot (falla cerrado) y se
  avisa al arrancar.
- Si añaden el bot a un grupo, supergrupo o canal, lo abandona.
- `main()` separa la construcción de la aplicación en `construir_app()`.
- Tests `tests/test_bot_acceso.py` y nuevo caso en `tests/test_config.py`.

### Se elimina la extracción de constantes por IA
- La fase 4 ya no pide a la IA una lista de constantes de código. Con
  `gpt-4o` se detectó que inventaba constantes inexistentes (llegó a copiar
  el ejemplo `GC_MODE` del propio prompt).
- La sección no llegaba al Word (la plantilla y el modo AFU ya no la tenían);
  solo aparecía en el JSON de respaldo, del que también se quita.
- Las constantes de negocio (ZTCONSTANTES y similares) siguen documentándose
  en la sección 5.3 Configuración, leídas directamente de la metadata, sin IA.
- Tests actualizados en `tests/test_pipeline.py`.

## 2026-10-13

### El registro de auditoría anota la causa cuando el pipeline se interrumpe
- Si la ejecución fallaba antes de terminar la primera fase (p. ej. el
  proveedor de IA rechaza la llamada por falta de saldo), el log adjunto decía
  solo "Sin logs registrados". Ahora incluye una entrada
  "ERROR - PIPELINE INTERRUMPIDO" con el tipo y el detalle del error.
- Test ampliado en `tests/test_pipeline.py`.

### Proveedor de IA seleccionable por configuración
- Nuevo `ia.py`: el núcleo solo conoce `ClienteIA.completar()`; ya no depende
  de la forma de las respuestas del SDK de OpenAI ni de parámetros de NVIDIA.
- Nueva variable `LLM_PROVIDER`: `nvidia` (por defecto, sin cambios),
  `openai_compatible` (p. ej. un NIM propio) o `azure_openai` (requiere
  `LLM_API_VERSION`; los modelos son nombres de despliegue).
- Nueva variable `LLM_EXTRA_BODY`: parámetros propios del proveedor. El
  `enable_thinking: false` de NVIDIA pasa a ser su valor por defecto en vez de
  estar escrito en el código.
- Una respuesta sin texto de la IA ya no rompe el Code Review.
- Tests `tests/test_ia.py` y nuevos casos en `tests/test_config.py`. La ruta
  de Azure OpenAI solo está probada con el cliente simulado, no contra un
  recurso real.

### La calibración fallida ya no se confunde con la omitida
- Antes, si la IA no devolvía un JSON válido para el documento de calibración
  (o el documento no tenía texto), el log decía "FASE 0 OMITIDA" y el usuario
  no se enteraba de que se había aplicado el estilo por defecto.
- Ahora el log distingue "OMITIDA" (decisión del usuario), "SIN TEXTO" y
  "FALLIDA" (con el modelo usado), y en los dos últimos casos el mensaje final
  del bot incluye un aviso.
- La consola registra `finish_reason` y los caracteres recibidos cuando la
  respuesta de calibración es ilegible.
- Tests en `tests/test_pipeline.py` y `tests/test_bot_mensajes.py`.

### Errores de red de Telegram sin traza en consola
- Manejador global de errores en `bot.py`: los cortes de red con Telegram
  (502 Bad Gateway, timeouts), que la librería reintenta sola, se registran en
  una línea de aviso en vez de una traza completa por intento. El resto de
  errores no controlados conserva su traza.
- Test `tests/test_bot_errores.py`.

### Configuración central validada al arrancar
- Nuevo `config.py`: único módulo que lee variables de entorno. Valida todo
  al iniciar y, si algo falta o es inválido, lista todos los problemas juntos
  y el bot no arranca.
- Nuevas variables: `LLM_BASE_URL` (proveedor de IA; exige https salvo
  servicio local) y `LLM_API_KEY` (`NVIDIA_API_KEY` sigue funcionando).
- Corrección: `LLM_MODEL_FASE0`, `LLM_MODEL_FASE1`, `LLM_MODEL_DEFAULT` (para
  esas dos fases) y `PLANTILLA_DTSS_PATH` definidos en el `.env` se ignoraban,
  porque se leían antes de cargar el archivo. Ahora se respetan.
- El token y la clave no aparecen si el objeto de configuración se imprime.
- `env.example` documenta todas las variables disponibles.
- Tests `tests/test_config.py`.

### Núcleo del pipeline separado del canal
- Nuevo `pipeline.py`: lectura del ZIP de la OT, clasificación de objetos,
  Code Review, fases de IA y armado del Word, sin ninguna dependencia de
  Telegram. Recibe una `EntradaPipeline` y devuelve un `ResultadoPipeline`.
- `bot.py` queda solo como canal: descarga archivos, muestra el progreso y
  envía los resultados. El cliente de IA, los modelos y la carpeta de salida
  se le pasan al núcleo como parámetros.
- El log de auditoría pasa a ser un objeto `RegistroAuditoria` (mismo texto).
- Tests `tests/test_pipeline.py` con una OT sintética y un cliente de IA falso,
  incluido uno que verifica que el núcleo no importa `telegram`.
- Sin cambios de comportamiento para quien usa el bot.

### Control de versiones y dependencias fijadas
- Se inicializa el repositorio git del proyecto.
- `requirements.txt` con las dependencias directas en versión fija y
  `requirements.lock` con el árbol completo instalado, para poder reproducir
  el entorno exacto.
- `requirements-dev.txt` con pytest para los tests.
- `.gitignore` ampliado: `.env.*`, `ajustes_tecnicos.json`, cachés de Python y pytest.
- Test `tests/test_dependencias.py`: falla si lo instalado no coincide con lo fijado.
- Sin cambios de comportamiento en el bot.
