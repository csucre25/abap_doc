import json
import pymupdf as fitz
from docx import Document
from openai import AsyncOpenAI
from messages import TEXTS

LANG = "es"

# El modelo por defecto (nemotron-3-super-120b-a12b) es un modelo de razonamiento
# con "thinking" activado por defecto. Con enable_thinking=True el modelo ya no
# continúa un mensaje "assistant" prefillado (genera su propio JSON completo
# desde cero), así que se apaga el razonamiento y se elimina el truco del prefill.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}

def extract_text_from_file(file_path: str) -> str:
    """Extrae el texto de un PDF o DOCX en milisegundos."""
    extension = file_path.lower().split('.')[-1]
    if extension == 'docx':
        doc = Document(file_path)
        raw_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
    elif extension == 'pdf':
        raw_text = ""
        with fitz.open(file_path) as pdf:
            for page in pdf:
                raw_text += page.get_text() + "\n"
    else:
        raise ValueError("Formato no soportado.")
    return raw_text[:15000]

def clean_json_response(raw_text: str) -> str:
    """Extrae de forma segura el JSON oculto dentro de texto conversacional."""
    text = raw_text.strip()
    
    # 1. Intentar extraer por bloques markdown
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
            
    # 2. Limpieza extrema: buscar la primera llave o corchete
    text = text.strip()
    start_idx = text.find('{')
    start_idx_array = text.find('[')
    
    # Encontrar cuál empieza primero (objeto o array)
    if start_idx != -1 and start_idx_array != -1:
        start_idx = min(start_idx, start_idx_array)
    elif start_idx == -1:
        start_idx = start_idx_array
        
    if start_idx != -1:
        text = text[start_idx:] # Cortar basura inicial
        # Buscar el último cierre
        end_idx = max(text.rfind('}'), text.rfind(']'))
        if end_idx != -1:
            text = text[:end_idx+1] # Cortar basura final
            
    return text.strip()

async def analyze_functional_text(raw_text: str, nvidia_client: AsyncOpenAI, modelo: str) -> dict:
    """Llama a la IA para analizar el texto funcional."""
    system_prompt = TEXTS[LANG]["sys_prompt_fase1"]
    user_prompt = TEXTS[LANG]["user_prompt_fase1"].format(texto_del_documento=raw_text)

    response = await nvidia_client.chat.completions.create(
        model=modelo,
        messages=[
            {"role": "system", "content": system_prompt}, 
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0, max_tokens=6000,
        extra_body=NO_THINKING,
    )
    
    # Con "enable_thinking": False el modelo genera el JSON completo por su
    # cuenta; ya no se concatena ningún prefijo manual.
    cleaned_response = clean_json_response(response.choices[0].message.content)
    try:
        return json.loads(cleaned_response)
    except json.JSONDecodeError as e:
        print(f"Error parseando AFU JSON: {e}\nTexto limpio: {cleaned_response[:100]}...")
        return {
            "document_type": "Desconocido",
            "general_objective": "Error de parseo IA",
            "requirements": [],
            "_debug_raw_response": cleaned_response[:1500],  # visible en el audit_log de Telegram
        }
    

async def analyze_calibration_text(raw_text: str, nvidia_client: AsyncOpenAI, modelo: str) -> dict:
    """Llama a la IA para analizar el texto de calibración."""
    system_prompt = TEXTS[LANG]["sys_prompt_fase0"]
    user_prompt = TEXTS[LANG]["user_prompt_fase0"].format(texto_historico=raw_text)

    response = await nvidia_client.chat.completions.create(
        model=modelo,
        messages=[
            {"role": "system", "content": system_prompt}, 
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0, max_tokens=2048,
        extra_body=NO_THINKING,
    )
    
    cleaned_response = clean_json_response(response.choices[0].message.content)
    try:
        return json.loads(cleaned_response)
    except json.JSONDecodeError as e:
        print(f"Error parseando Calibración JSON: {e}\nTexto limpio: {cleaned_response[:100]}...")
        return {}