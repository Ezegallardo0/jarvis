# Habilidades personalizadas (`custom_skills`)

Colocá aquí carpetas de habilidades personalizadas con:
- `manifest.json` (con `id`, `name`, `version`, `description`, `author`, `permissions`, `tools`)
- `skill.py` (con `register(contexto)`)

Por seguridad, las habilidades personalizadas inician **desactivadas** hasta que el usuario las revise y active explícitamente desde el HUD o `SkillsManager`.
