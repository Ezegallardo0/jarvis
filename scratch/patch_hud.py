import re

path = r'c:\Users\plumi\Downloads\jarvis - copia\hud\index.html'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add Cursor to cfg-proveedor
old_proveedor = '''        <select id="cfg-proveedor">
          <option value="ollama">Ollama · local</option>
          <option value="gemini">Gemini</option>
          <option value="grok">Grok · xAI</option>
          <option value="openai">OpenAI</option>
        </select>'''

new_proveedor = '''        <select id="cfg-proveedor">
          <option value="gemini">Gemini</option>
          <option value="grok">Grok · xAI</option>
          <option value="openai">OpenAI</option>
          <option value="cursor">Cursor · Grok Free</option>
          <option value="ollama">Ollama · local</option>
        </select>'''

# 2. Add Cursor to fallback
old_fallback = '''        <select id="cfg-ai-fallback">
          <option value="">Desactivado</option>
          <option value="gemini">Gemini</option>
          <option value="grok">Grok · xAI</option>
          <option value="openai">OpenAI</option>
        </select>'''

new_fallback = '''        <select id="cfg-ai-fallback">
          <option value="">Desactivado</option>
          <option value="gemini">Gemini</option>
          <option value="grok">Grok · xAI</option>
          <option value="openai">OpenAI</option>
          <option value="cursor">Cursor · Grok Free</option>
        </select>'''

# 3. Add Ensemble controls card
ensemble_card = '''
      <section class="settings-card">
        <h3>TRABAJO EN CONJUNTO (ENSEMBLE MULTI-IA)</h3>
        <p class="hint">Al configurar 2 o más API keys (Gemini, Grok, OpenAI, Cursor), Jarvis las hace trabajar juntas para mejores respuestas.</p>
        <label for="cfg-modo-ensemble">Modo Ensemble</label>
        <select id="cfg-modo-ensemble">
          <option value="auto">Automático (activa al tener 2 o más claves)</option>
          <option value="on">Siempre activo</option>
          <option value="off">Desactivado (solo proveedor principal)</option>
        </select>
        <label for="cfg-estrategia-ensemble">Estrategia de cooperación</label>
        <select id="cfg-estrategia-ensemble">
          <option value="mejor">Mejor respuesta · síntesis en paralelo (recomendado)</option>
          <option value="rapido">Más rápido · el primero que responda gana</option>
          <option value="verificar">Verificación cruzada · principal responde, otros corrigen</option>
          <option value="votar">Votación por consenso (requiere 3+ IAs)</option>
        </select>
        <p class="hint" id="cfg-ensemble-status">IAs configuradas activas: ninguna</p>
      </section>'''

# 4. Add modelo-cursor to models section
old_models = '''        <label for="cfg-modelo-openai">Modelo de OpenAI</label>
        <input id="cfg-modelo-openai" list="cfg-modelos-openai" autocomplete="off">
        <datalist id="cfg-modelos-openai"></datalist>'''

new_models = '''        <label for="cfg-modelo-openai">Modelo de OpenAI</label>
        <input id="cfg-modelo-openai" list="cfg-modelos-openai" autocomplete="off">
        <datalist id="cfg-modelos-openai"></datalist>
        <label for="cfg-modelo-cursor">Modelo de Cursor (Grok Free)</label>
        <input id="cfg-modelo-cursor" list="cfg-modelos-cursor" autocomplete="off">
        <datalist id="cfg-modelos-cursor"></datalist>'''

# 5. Update JS to populate cursor models and ensemble fields
old_js_loop = "for(const proveedor of ['gemini','grok','openai']){"
new_js_loop = "for(const proveedor of ['gemini','grok','openai','cursor']){"

# Replace 1
content = content.replace(old_proveedor, new_proveedor, 1)
# Replace 2
content = content.replace(old_fallback, new_fallback, 1)
# Insert ensemble card before VOZ DE RESPUESTA
target_voice = '<section class="settings-card">\n        <h3>VOZ DE RESPUESTA</h3>'
if target_voice not in content:
    target_voice = '<section class="settings-card">\r\n        <h3>VOZ DE RESPUESTA</h3>'
content = content.replace(target_voice, ensemble_card + '\n      ' + target_voice, 1)
# Replace 4
content = content.replace(old_models, new_models, 1)
# Replace 5
content = content.replace(old_js_loop, new_js_loop, 1)

# In JS abrirAjustes: add ensemble and cursor bindings
old_js_abrir = "    $('cfg-modelo-openai').value = d.modelo_openai || '';"
new_js_abrir = """    $('cfg-modelo-openai').value = d.modelo_openai || '';
    $('cfg-modelo-cursor').value = d.modelo_cursor || '';
    $('cfg-modo-ensemble').value = d.modo_ensemble || 'auto';
    $('cfg-estrategia-ensemble').value = d.estrategia_ensemble || 'mejor';
    const activas = d.ensemble_proveedores_activos || [];
    $('cfg-ensemble-status').textContent = activas.length > 0
      ? `IAs activas para trabajo en conjunto (${activas.length}): ${activas.join(', ')}`
      : 'Necesitas al menos 1 API key configurada.';"""
content = content.replace(old_js_abrir, new_js_abrir, 1)

# In configuracionFormulario: add cursor and ensemble fields
old_js_form = "    modelo_openai:$('cfg-modelo-openai').value,"
new_js_form = """    modelo_openai:$('cfg-modelo-openai').value,
    modelo_cursor:$('cfg-modelo-cursor').value,
    modo_ensemble:$('cfg-modo-ensemble').value,
    estrategia_ensemble:$('cfg-estrategia-ensemble').value,"""
content = content.replace(old_js_form, new_js_form, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('OK: index.html patched successfully')

