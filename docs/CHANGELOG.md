# Changelog

## 2026-10-13

### Control de versiones y dependencias fijadas
- Se inicializa el repositorio git del proyecto.
- `requirements.txt` con las dependencias directas en versión fija y
  `requirements.lock` con el árbol completo instalado, para poder reproducir
  el entorno exacto.
- `requirements-dev.txt` con pytest para los tests.
- `.gitignore` ampliado: `.env.*`, `ajustes_tecnicos.json`, cachés de Python y pytest.
- Test `tests/test_dependencias.py`: falla si lo instalado no coincide con lo fijado.
- Sin cambios de comportamiento en el bot.
