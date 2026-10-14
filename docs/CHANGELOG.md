# Changelog

## 2026-10-13

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
