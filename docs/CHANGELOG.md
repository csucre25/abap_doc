# Changelog

## 2026-10-14

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
