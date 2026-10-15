# sapdoc: documentación técnica (DTSS) de OTs SAP ABAP con IA

Bot de Telegram (python-telegram-bot 22, long-polling) que recibe el ZIP de una
orden de transporte y opcionalmente un documento de estilo y el AFU, analiza el
ABAP con IA (NVIDIA, Azure OpenAI o servicio compatible con OpenAI) y devuelve
el DTSS en Word (python-docx/docxtpl). Python 3.13, Windows, PowerShell.
Detalle del flujo en README.md.

## Comandos (siempre desde la raíz, con el venv)
- Tests: `.\venv\Scripts\python.exe -m pytest -q -p no:warnings`
- Bot: `.\venv\Scripts\python.exe -m sapdoc`
- Verificar auditoría: `.\venv\Scripts\sapdoc-verificar-auditoria auditoria\eventos.jsonl`

## Arquitectura (src/sapdoc)
- `domain/`: reglas puras (clasificación de objetos, ruido SAP, reparación del JSON de la IA, entidades).
- `application/`: casos de uso (`documentar_ot.py` es el principal), `puertos.py` (ClienteIA, GeneradorDocumento), `textos.py` (mensajes y prompts).
- `infrastructure/`: `config.py`, `ia.py`, `zip_ot.py`, `lectura_documentos.py`, `bitacora.py`, `word/`.
- `interfaces/telegram/bot.py`: el canal.
- Las dependencias solo van hacia dentro (interfaces → infrastructure → application → domain); `tests/test_arquitectura.py` lo exige.

## Reglas de cambio
- Una tarea por vez, con test y entrada en `docs/CHANGELOG.md` (sección por fecha, lo más nuevo arriba). Un commit por tarea, en la rama de trabajo, no en master.
- Todo en español: código, comentarios, mensajes, commits y respuestas al usuario.
- Toda función y clase lleva docstring (lo exige un test).
- Solo `infrastructure/config.py` lee el entorno. Variable nueva: `config.py` + `env.example` + test en `tests/test_config.py`.
- Textos visibles y prompts van en `application/textos.py`, no sueltos en el código.

## Tests
- Usan dobles: `ClienteIAFalso` (sin llamadas reales a la IA), `GeneradorWord` con la plantilla en ruta absoluta y `USUARIOS_PERMITIDOS_PATH` apuntando a un archivo inexistente, para no leer datos reales.
- `usuarios_permitidos.example.json` debe seguir siendo válido (hay un test).

## Datos sensibles: no leer ni commitear salvo que el usuario lo pida
`.env` (bloqueado), `usuarios_permitidos.json`, `auditoria/`, `salida_prueba/` (salidas reales de prueba).

## Ruido conocido (no es un error)
- Avisos de git "LF will be replaced by CRLF".
- `PTBUserWarning` sobre `per_message` del ConversationHandler.

## Pendiente (tarea de seguridad)
Carpeta de trabajo aislada por ejecución con nombres saneados; límites de tamaño, ZIP y usos por usuario; Code Review que bloquee si no se puede leer y que no reciba filas de customizing.
