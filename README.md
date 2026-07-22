# 🤖 Cozmo Companion

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-GPLv3-green)
![Status](https://img.shields.io/badge/status-verificado%20%E2%9C%93-brightgreen)

**Reemplazo moderno de la app oficial de Cozmo (descontinuada por Anki).** Controla tu Cozmo **sin la app móvil**: conexión directa por WiFi con [pycozmo](https://github.com/zayfod/pycozmo), panel web en tiempo real con WebSocket, conversación con IA local y comandos de voz offline — todo bilingüe **ES/EN**.

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
- **Conversación con IA local** — Ollama con **8 personalidades bilingües**: amigable, Ted (humor negro), pirata, sabio, destructor, anime, depresivo y bebé. Cozmo *habla* las respuestas por su altavoz.
- **Comandos de voz offline** — Vosk + push-to-talk desde la web. Parser ES/EN: *"cozmo avanza 2"*, *"baila"*, *"luces rojas"*, *"di hola mundo"*. Lo que no es comando se envía al LLM como conversación.
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
                              └─→ OllamaClient → localhost:11434
```

| Módulo | Responsabilidad |
|---|---|
| `companion/robot.py` | Wrapper thread-safe de pycozmo: acciones serializadas en un executor, nunca se solapan en el cable |
| `companion/server.py` | FastAPI + WebSocket hub; loops de cámara (5 fps) y estado (2 s) |
| `companion/emotions.py` | Estado emocional con callback `on_change`; modificadores de prompt ES/EN |
| `companion/llm.py` | Cliente Ollama con urllib (cero dependencias extra) + personalidades |
| `companion/memory.py` | SQLite: interacciones persistentes + contexto conversacional para el LLM |
| `companion/pet_mode.py` | Hilo de comportamiento autónomo guiado por la emoción actual |
| `companion/perception.py` | Detección de caras con Haar cascades + cálculo de giro para centrarse |
| `companion/recorder.py` | Grabación de video: MP4 con OpenCV, GIF animado con Pillow |
| `companion/stt.py` | Hilo Vosk: push-to-talk y modo continuo para wake-word |
| `companion/commands.py` | Parser bilingüe con números escritos (uno..diez / one..ten) y colores |
| `companion/config.py` | Dataclass + variables de entorno `COZMO_*` + descubrimiento de modelo Vosk |
| `companion/static/` | UI vanilla JS/CSS/HTML — sin build, sin frameworks |

## 📋 Requisitos

1. **Python 3.10+** (pycozmo funciona en Python moderno, a diferencia del SDK oficial).
2. **Windows: [Npcap](https://npcap.com)** — necesario para la captura de paquetes de pycozmo. Linux: ejecutar con `sudo` o capabilities de red (`setcap`).
3. **Opcional — IA:** [Ollama](https://ollama.com) + `ollama pull phi3`.
4. **Opcional — voz:** micrófono + modelo Vosk (ver abajo).
5. **Opcional — visión:** `opencv-python-headless<5` (ya en requirements) para detección de caras y video MP4. Sin OpenCV la app funciona igual, grabando GIFs.

## 🚀 Instalación y ejecución

```bash
git clone https://github.com/vicorio27/cozmo-companion.git
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
python run.py --ollama-url URL         # Ollama remoto
python run.py --vosk-model RUTA        # modelo Vosk específico
python run.py --fps 8                  # más fluidez de cámara
python run.py --no-browser             # no abrir el navegador
python run.py --log-level DEBUG        # logs detallados
```

También con variables de entorno: `COZMO_PORT`, `COZMO_LANG`, `COZMO_OLLAMA_URL`, `COZMO_OLLAMA_MODEL`, `COZMO_VOSK_MODEL`.

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

Números también como palabra: *"avanza dos"*, *"gira tres"*. Si el texto **no es un comando**, se envía al LLM como conversación (si Ollama está activo).

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
