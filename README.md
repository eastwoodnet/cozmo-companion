# 🤖 Cozmo Companion

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-GPLv3-green)
![Status](https://img.shields.io/badge/status-verificado%20%E2%9C%93-brightgreen)

**Reemplazo moderno de la app oficial de Cozmo (descontinuada por Anki).** Controla tu Cozmo **sin la app móvil**: conexión directa por WiFi con [pycozmo](https://github.com/zayfod/pycozmo), panel web en tiempo real con WebSocket, conversación con IA local y comandos de voz offline — todo **ES/EN/中文**.

Reescritura moderna de [Cozmo Voice Commands](https://github.com/rizal72/Cozmo-Voice-Commands): el original quedó atado a Python 3.6 por el SDK de Anki; este proyecto usa **pycozmo sobre Python 3.10+**, FastAPI y arquitectura reactiva.

## ✅ Estado: verificado en vivo

Suite de pruebas de humo ejecutada contra el servidor real (sin robot físico):

- ✔️ Servidor arranca; `/`, `/api/health` y estáticos responden 200
- ✔️ 8/8 tests WebSocket: status, comandos, mood, errores, emociones, idioma, personalidad, ping/pong
- ✔️ Chat LLM de extremo a extremo con Ollama (`phi3`)
- ✔️ Parser de comandos ES/EN (avanza, baila, luces rojas, di hola, cómo estás...)
- ✔️ Auto-descubrimiento del modelo Vosk desde el repo hermano Cozmo-Voice-Commands

## ✨ Características

- **Panel web en tiempo real** — WebSocket puro, sin recargas ni polling: cámara en vivo (5 fps), estado, batería, log.
- **Control total del robot** — D-pad con hold-to-move + teclado (WASD/flechas), brazo y cabeza con sliders, luces RGB con paleta y selector de color, animaciones, voz TTS, fotos.
- **Conversación con IA** — Ollama local o LLM en la nube (OpenAI-compatible), con **8 personalidades bilingües**: amigable, Ted (humor negro), pirata, sabio, destructor, anime, depresivo y bebé. Cozmo *habla* las respuestas por su altavoz.
- **Comandos de voz offline** — Vosk + push-to-talk desde la web. Parser ES/EN/中文: *"cozmo avanza 2"*, *"baila"*, *"luces rojas"*, *"di hola mundo"*, *"前进二"*, *"跳舞"*. Lo que no es comando se envía al LLM como conversación.
- **Sistema emocional** — 7 emociones (feliz, triste, curioso, emocionado, cansado, aburrido, asustado) que cambian luces, animaciones y el prompt del LLM. Se aburre si lo ignoras.
- **Modo mascota autónomo 🐾** — si lo activas y nadie juega con él, Cozmo actúa por su cuenta según su estado de ánimo: explora cuando está curioso, baila cuando está feliz, pide atención cuando se aburre, baja la cabeza cuando tiene sueño... Nunca interrumpe: detecta cuándo estás interactuando.
- **Memoria persistente 🧠** — SQLite guarda cada conversación, comando y evento en `data/companion.db`. Las últimas interacciones se inyectan en el prompt del LLM: **Cozmo recuerda de qué hablabais**, incluso entre sesiones. Contador en la UI con botón de borrado.
- **Grabación de video ⏺️** — graba lo que ve Cozmo en MP4 (OpenCV) o GIF (fallback con Pillow). Se guarda en `recordings/` y se sirve en `/recordings/<archivo>`.
- **Detección de caras 👤** — OpenCV (Haar cascades incluidos, sin descargas). Cuando Cozmo te ve aparecer, se emociona, lo anuncia y **gira para mirarte** si estás descentrado. Con histéresis anti-parpadeo.
- **Wake-word 🗣️** — escucha continua offline con Vosk: di **«Cozmo»** seguido de un comando (*"¡Cozmo, baila!"*) o solo *"Cozmo"* para que responda *"¿Sí?"*. Sin nubes, sin APIs externas.
- **Fotos** — captura desde la cámara de Cozmo: se guardan en `photos/` y se descargan al navegador.
- **UI bilingüe** — español/inglés con un clic (botón EN/ES).

## 🏗️ Arquitectura

```
Navegador ←—WebSocket—→ FastAPI (Hub, asyncio)
                              │
                              ├─→ ThreadPoolExecutor(1) → pycozmo → Cozmo (WiFi UDP)
                              ├─→ VoskListener (thread) → micrófono
                              └─→ LLMClient → Ollama (localhost:11434) / OpenAI-compatible (nube)
```

| Módulo | Responsabilidad |
|---|---|
| `companion/robot.py` | Wrapper thread-safe de pycozmo: acciones serializadas en un executor, nunca se solapan en el cable |
| `companion/server.py` | FastAPI + WebSocket hub; loops de cámara (5 fps) y estado (2 s) |
| `companion/emotions.py` | Estado emocional con callback `on_change`; modificadores de prompt ES/EN |
| `companion/llm.py` | Cliente LLM con urllib (cero dependencias extra): Ollama local u OpenAI-compatible (nube) + personalidades |
| `companion/memory.py` | SQLite: interacciones persistentes + contexto conversacional para el LLM |
| `companion/pet_mode.py` | Hilo de comportamiento autónomo guiado por la emoción actual |
| `companion/perception.py` | Detección de caras con Haar cascades + cálculo de giro para centrarse |
| `companion/recorder.py` | Grabación de video: MP4 con OpenCV, GIF animado con Pillow |
| `companion/stt.py` | Hilo Vosk: push-to-talk y modo continuo para wake-word |
| `companion/commands.py` | Parser ES/EN/中文 con números escritos (uno..diez / one..ten / 一..十) y colores |
| `companion/config.py` | Dataclass + variables de entorno `COZMO_*` + descubrimiento de modelo Vosk |
| `companion/static/` | UI vanilla JS/CSS/HTML — sin build, sin frameworks |

## 📋 Requisitos

1. **Python 3.10+** (pycozmo funciona en Python moderno, a diferencia del SDK oficial).
2. **Windows: [Npcap](https://npcap.com)** — necesario para la captura de paquetes de pycozmo. Linux: ejecutar con `sudo` o capabilities de red (`setcap`).
3. **Opcional — IA:** [Ollama](https://ollama.com) + `ollama pull phi3` (o un LLM OpenAI-compatible en la nube, ver [LLM en la nube](#-llm-en-la-nube-openai-compatible)).
4. **Opcional — voz:** micrófono + modelo Vosk (ver abajo).
5. **Opcional — visión:** `opencv-python-headless<5` (ya en requirements) para detección de caras y video MP4. Sin OpenCV la app funciona igual, grabando GIFs.

## 🚀 Instalación y ejecución

```bash
git clone https://github.com/eastwoodnet/cozmo-companion.git
cd cozmo-companion
pip install -r requirements.txt
python run.py
```

Se abre automáticamente en `http://127.0.0.1:8000`.

**Para conectar con el robot:**
1. Enciende Cozmo (levanta su brazo: emite su propia red WiFi).
2. Conecta tu PC a la red WiFi de Cozmo (ej. `Cozmo_XXXXXX`).
3. Pulsa **«Conectar»** en el panel web.

### Opciones

```
python run.py --port 9000              # otro puerto
python run.py --lang en                # interfaz en inglés
python run.py --ollama-model llama3.2  # otro modelo de Ollama
python run.py --ollama-url URL         # Ollama remoto (o base URL OpenAI-compatible con API key)
python run.py --llm-api-key "sk-..."   # activa modo nube (mejor vía COZMO_LLM_API_KEY)
python run.py --vosk-model RUTA        # modelo Vosk específico
python run.py --fps 8                  # más fluidez de cámara
python run.py --no-browser             # no abrir el navegador
python run.py --log-level DEBUG        # logs detallados
```

También con variables de entorno: `COZMO_PORT`, `COZMO_LANG`, `COZMO_OLLAMA_URL`, `COZMO_OLLAMA_MODEL`, `COZMO_LLM_API_KEY`, `COZMO_VOSK_MODEL`.

## ☁️ LLM en la nube (OpenAI-compatible)

El cliente LLM no está atado a Ollama: si defines una API key, usa cualquier endpoint **OpenAI-compatible** (`/chat/completions`, auth Bearer) en vez de Ollama local.

```bash
# La base URL debe incluir /v1 (convención OpenAI)
export COZMO_OLLAMA_URL="https://api.tu-proveedor.com/v1"
export COZMO_OLLAMA_MODEL="tu-modelo"
export COZMO_LLM_API_KEY="sk-..."
python run.py
```

- Sin `COZMO_LLM_API_KEY` → modo Ollama local (`/api/generate`), comportamiento anterior.
- Con `COZMO_LLM_API_KEY` → modo OpenAI-compatible (nube), auth Bearer.
- La API key **no** se registra en logs ni se muestra en la UI.
- Compatible con cualquier proveedor OpenAI-compatible: OpenAI, DeepSeek, 通义千问 (DashScope), Kimi, GLM, Azure, etc.

## 🎙️ Modelo de voz (Vosk)

El recognizer busca automáticamente un modelo en este orden:
1. Variable de entorno `COZMO_VOSK_MODEL` (o `--vosk-model`)
2. `./models/` de este proyecto
3. `../Cozmo-Voice-Commands/cvc/models/vosk/` (repo hermano, si existe)

Descarga recomendada (**español**, ~39 MB):

```bash
mkdir models && cd models
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip
unzip vosk-model-small-es-0.42.zip
```

Inglés: [vosk-model-small-en-us-0.15](https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip) (~40 MB).

Chino: [vosk-model-small-cn-0.22](https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip) (~42 MB) — ver [中文支持](#-中文支持-chinese).

## 🇨🇳 中文支持 (Chinese)

Cozmo Companion soporta diálogo y comandos de voz en chino. No requiere cambios de código: solo configurar dos modelos.

### 1. Reconocimiento de voz chino (Vosk)

Descarga el modelo chino (~42 MB):

```bash
mkdir models && cd models
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip
unzip vosk-model-small-cn-0.22.zip
```

Arranca con:

```bash
python run.py --vosk-model ./models/vosk-model-small-cn-0.22
# o: COZMO_VOSK_MODEL=./models/vosk-model-small-cn-0.22 python run.py
```

También hay un modelo grande para mayor precisión (servidor): [vosk-model-cn-0.22](https://alphacephei.com/vosk/models/vosk-model-cn-0.22.zip) (~1.3 GB).

### 2. Diálogo chino (Ollama)

El modelo por defecto `phi3` tiene un chino débil. Para conversar en chino, descarga un modelo con buen chino:

```bash
ollama pull qwen2.5          # 7B, recomendado
# ollama pull qwen2.5:3b     # ligero
# ollama pull qwen2.5:1.5b   # muy ligero
```

Arranca con:

```bash
python run.py --ollama-model qwen2.5
```

Las 8 personalidades siguen funcionando: el prompt de personalidad es ES/EN, pero el modelo responde en el idioma que hables (entrada china → respuesta en chino).

### 3. Comandos de voz en chino

El parser reconoce comandos en chino (los números también como carácter: *"前进二"*, *"左转三"*):

| Chino | Acción |
|---|---|
| `前进 2` / `前进二` | Avanzar N segundos |
| `后退` | Retroceder |
| `左转 45` / `右转` | Girar N grados |
| `跳舞` | Bailar |
| `看看周围` | Mirar alrededor |
| `拍照` | Tomar foto |
| `说 你好` | Hablar (TTS) |
| `红灯` / `关灯` | Cambiar luces |
| `睡觉` | A dormir |
| `开心` / `难过` | Cambiar emoción |
| `你怎么样` | Reportar su estado de ánimo |
| `举起手臂` / `放下手臂` | Brazo arriba/abajo |
| `抬头` / `低头` | Cabeza arriba/abajo |

### ⚠️ Limitaciones

- **El TTS de Cozmo no habla chino**: el altavoz del robot está optimizado para inglés/español. Las respuestas chinas se muestran en el panel web, pero la voz del robot suena en inglés (o con pronunciación imprecisa).
- La detección automática de emociones solo reconoce palabras clave ES/EN; el texto en chino no cambia la emoción automáticamente (puedes cambiarla a mano desde la UI).
- `"说X"` también es una frase común en chino conversacional (p. ej. *"说个笑话"* = "cuenta un chiste") y puede interpretarse como comando "decir X" en vez de enviarse al LLM. Si te ocurre, usa la interfaz web o reformula.

## 🎮 Comandos de voz / texto

| Español | English | Acción |
|---|---|---|
| `cozmo avanza 2` | `cozmo forward 2` | Avanzar N segundos |
| `atrás` | `backward` | Retroceder |
| `izquierda 45` | `left 45` | Girar N grados |
| `baila` | `dance` | Bailar |
| `mira` / `explora` | `look around` | Mirar alrededor |
| `foto` | `photo` | Tomar foto |
| `di hola mundo` | `say hello` | Hablar (TTS) |
| `luces rojas` | `lights red` | Cambiar luces |
| `duerme` | `sleep` | A dormir |
| `feliz` / `triste` | `happy` / `sad` | Cambiar emoción |
| `cómo estás` | `how are you` | Reportar su estado de ánimo |
| `sube brazo` | `lift up` | Brazo arriba/abajo |
| `cabeza arriba` | `head up` | Cabeza arriba/abajo |

Números también como palabra: *"avanza dos"*, *"gira tres"*. Si el texto **no es un comando**, se envía al LLM como conversación (si el LLM está activo).

## 🧩 Estructura del proyecto

```
cozmo-companion/
├── run.py                  # launcher CLI (python run.py)
├── requirements.txt
├── LICENSE                 # GPL v3
├── photos/                 # fotos capturadas (git-ignored)
├── recordings/             # videos grabados (git-ignored)
├── models/                 # (opcional) modelos Vosk (git-ignored)
├── data/                   # memoria SQLite (git-ignored)
└── companion/
    ├── __init__.py         # __version__
    ├── config.py
    ├── robot.py
    ├── server.py
    ├── emotions.py
    ├── llm.py
    ├── memory.py
    ├── pet_mode.py
    ├── perception.py
    ├── recorder.py
    ├── stt.py
    ├── commands.py
    └── static/
        ├── index.html
        ├── style.css
        └── app.js
```

## 🔧 Troubleshooting

| Problema | Solución |
|---|---|
| *"No se pudo conectar"* | Verifica que el PC está en la WiFi de Cozmo y que Npcap está instalado (Windows) |
| *"Ollama no responde"* | `ollama serve` en otra terminal + `ollama pull phi3` |
| *"No se pudo conectar con el LLM"* (nube) | Revisa `COZMO_OLLAMA_URL` (debe incluir `/v1`) y `COZMO_LLM_API_KEY` |
| El botón 🎤 no aparece | Falta modelo Vosk o PyAudio — revisa el banner de arranque |
| Emojis rotos en consola Windows | Ya gestionado: `run.py` reconfigura stdout a UTF-8 |
| Giro impreciso | Calibración `TURN_DEG_PER_SEC = 130` en `robot.py` — ajústala a tu unidad |
| Voz TTS suena rara en español | Limitación del sintetizador de Cozmo (optimizado para inglés) |

## ⚠️ Limitaciones de pycozmo

- No hay navegación al cargador ni detección de cargador.
- Detección de caras limitada (sin nombres).
- Algunas animaciones del SDK original pueden no existir (se ignoran con un warning en el log).

## 🗺️ Roadmap

- [x] ~~Modo mascota autónomo (idle behaviors según emoción)~~ — v1.1.0
- [x] ~~Memoria persistente de conversaciones (SQLite)~~ — v1.1.0
- [x] ~~Grabación de video~~ — v1.2.0 (MP4/GIF)
- [x] ~~Detección de caras con la cámara~~ — v1.2.0 (reacciona y te mira)
- [x] ~~Wake-word ("¡Cozmo!") sin pulsar botón~~ — v1.2.0 (Vosk continuo, offline)
- [ ] Detección de objetos genérica (YOLO/DNN)
- [ ] Reconocimiento de caras con nombres
- [ ] Triggers automáticos por eventos (cara → foto, batería baja → aviso)

## Créditos

- [pycozmo](https://github.com/zayfod/pycozmo) — la magia de la conexión directa.
- [Cozmo Voice Commands](https://github.com/rizal72/Cozmo-Voice-Commands) de Riccardo Sallusti — base conceptual, personalidades y calibraciones.
- [Vosk](https://alphacephei.com/vosk/) — STT offline. [Ollama](https://ollama.com) — LLM local.

## Licencia

GPL v3 (derivado de Cozmo Voice Commands).
