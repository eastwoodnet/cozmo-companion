r"""FastAPI server for Cozmo Companion: REST + WebSocket real-time hub.

Architecture:
    Browser <--WebSocket--> Hub (asyncio) <--executor thread--> pycozmo Client
                                      \---- background thread --> Vosk STT

The WebSocket protocol is JSON both ways. Client messages carry an "action";
server messages carry a "type" (status, camera, log, chat, stt, photo, error).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from pathlib import Path
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .commands import COLORS, Command, parse
from .config import Config
from .emotions import EmotionState, emotion_list
from .llm import LLMClient
from .memory import KIND_COMMAND, KIND_COZMO, KIND_EVENT, KIND_USER, Memory
from .perception import FaceDetector, faces_available, turn_angle_for_face
from .pet_mode import PetMode
from .recorder import VideoRecorder
from .robot import Robot
from .stt import VoskListener, stt_available

log = logging.getLogger("companion.server")

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Localized emotion names for spoken mood reports.
EMOTION_NAMES = {
    "happy": {"es": "feliz", "en": "happy"},
    "sad": {"es": "triste", "en": "sad"},
    "curious": {"es": "curioso", "en": "curious"},
    "excited": {"es": "emocionado", "en": "excited"},
    "tired": {"es": "cansado", "en": "tired"},
    "bored": {"es": "aburrido", "en": "bored"},
    "scared": {"es": "asustado", "en": "scared"},
}

LOG_STRINGS = {
    "es": {
        "connecting": "Conectando con Cozmo por WiFi...",
        "connected": "¡Cozmo conectado!",
        "connect_failed": "No se pudo conectar. ¿Estás en la WiFi de Cozmo?",
        "disconnected": "Cozmo desconectado",
        "need_robot": "Cozmo no está conectado",
        "cmd_not_recognized": "No reconocí ese comando",
        "stt_unavailable": "Voz no disponible (falta modelo Vosk o micrófono)",
        "llm_down": "Ollama no responde. Inícialo con: ollama serve",
        "pet_on": "Modo mascota activado 🐾",
        "pet_off": "Modo mascota desactivado",
        "memory_cleared": "Memoria borrada 🧠✕",
        "wake_on": "Wake-word activado: di «Cozmo» seguido de un comando 🗣️",
        "wake_off": "Wake-word desactivado",
        "wake_busy": "El wake-word ya está escuchando; pulsa el 🎤 para parar",
        "faces_on": "Detección de caras activada 👤",
        "faces_off": "Detección de caras desactivada",
        "face_seen": "¡Te veo! 👤",
        "rec_on": "⏺️ Grabando video...",
        "rec_saved": "🎬 Video guardado",
        "rec_empty": "No hay frames que guardar (¿cámara activada?)",
    },
    "en": {
        "connecting": "Connecting to Cozmo over WiFi...",
        "connected": "Cozmo connected!",
        "connect_failed": "Could not connect. Are you on Cozmo's WiFi?",
        "disconnected": "Cozmo disconnected",
        "need_robot": "Cozmo is not connected",
        "cmd_not_recognized": "Command not recognized",
        "stt_unavailable": "Voice unavailable (missing Vosk model or mic)",
        "llm_down": "Ollama is not responding. Start it with: ollama serve",
        "pet_on": "Pet mode enabled 🐾",
        "pet_off": "Pet mode disabled",
        "memory_cleared": "Memory cleared 🧠✕",
        "wake_on": "Wake-word enabled: say \"Cozmo\" followed by a command 🗣️",
        "wake_off": "Wake-word disabled",
        "wake_busy": "Wake-word is already listening; press 🎤 to stop",
        "faces_on": "Face detection enabled 👤",
        "faces_off": "Face detection disabled",
        "face_seen": "I can see you! 👤",
        "rec_on": "⏺️ Recording video...",
        "rec_saved": "🎬 Video saved",
        "rec_empty": "No frames to save (is the camera on?)",
    },
    "zh": {
        "connecting": "正在通过 WiFi 连接 Cozmo...",
        "connected": "Cozmo 已连接！",
        "connect_failed": "连接失败。你连的是 Cozmo 的 WiFi 吗？",
        "disconnected": "Cozmo 已断开",
        "need_robot": "Cozmo 未连接",
        "cmd_not_recognized": "没听懂这个命令",
        "stt_unavailable": "语音不可用（缺少 Vosk 模型或麦克风）",
        "llm_down": "LLM 无响应",
        "pet_on": "宠物模式已开启 🐾",
        "pet_off": "宠物模式已关闭",
        "memory_cleared": "记忆已清除 🧠✕",
        "wake_on": "唤醒词已开启：说「Cozmo」加命令 🗣️",
        "wake_off": "唤醒词已关闭",
        "wake_busy": "唤醒词已在监听；按 🎤 停止",
        "faces_on": "人脸识别已开启 👤",
        "faces_off": "人脸识别已关闭",
        "face_seen": "我看到你了！👤",
        "rec_on": "⏺️ 正在录像...",
        "rec_saved": "🎬 视频已保存",
        "rec_empty": "没有可保存的画面（相机开了吗？）",
    },
}

WAKE_WORDS = ("cozmo", "cosmo", "小智")
WAKE_ACKS = {
    "es": ["¿Sí?", "¿Dime?", "¿Qué pasa?"],
    "en": ["Yes?", "I'm listening", "What is it?"],
    "zh": ["嗯？", "我在听", "怎么了？"],
}


class Hub:
    """Application state and message routing for all connected clients."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.lang = config.language if config.language in LOG_STRINGS else "es"
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.clients: Set[WebSocket] = set()

        self.robot = Robot()
        self.emotions = EmotionState(on_change=self._on_emotion_change)
        self.llm = LLMClient(config.ollama_url, config.ollama_model, api_key=config.llm_api_key)
        self.memory = Memory(config.data_dir / "companion.db")
        self.pet = PetMode(
            self.robot,
            self.emotions,
            lang=self.lang,
            on_action=lambda desc: self.broadcast_ts(
                {"type": "log", "level": "info", "message": f"🐾 {desc}"}
            ),
        )

        self._stt_available = stt_available(config.vosk_model)
        self.stt: Optional[VoskListener] = self._make_listener(continuous=False)
        self.stt_wake: Optional[VoskListener] = None  # created on demand
        self.wake_mode = False

        # Video recording + face detection
        self.recorder = VideoRecorder(fps=config.camera_fps)
        self._faces_available = faces_available()
        self._face_detector: Optional[FaceDetector] = None
        if self._faces_available:
            try:
                self._face_detector = FaceDetector()
            except RuntimeError as e:
                log.warning("Face detector disabled: %s", e)
                self._faces_available = False
        self.faces_enabled = False
        self._face_present = False
        self._face_absent_checks = 0
        self._face_task = None

        self._llm_ok = False
        self._llm_checked = 0.0
        self._reconnecting = False
        self._tasks = []

    def _make_listener(self, continuous: bool) -> Optional[VoskListener]:
        """Build a Vosk listener; continuous mode is used for the wake-word."""
        if not self._stt_available:
            return None
        if continuous:
            return VoskListener(
                self.config.vosk_model,
                continuous=True,
                on_final=lambda t: self._run_coro(self._on_wake_utterance(t)),
                on_error=lambda m: self.send_error_ts(m),
            )
        return VoskListener(
            self.config.vosk_model,
            on_partial=lambda t: self.broadcast_ts({"type": "stt", "state": "listening", "partial": t}),
            on_final=lambda t: self.broadcast_ts({"type": "stt", "state": "off", "final": t})
            and self._run_coro(self._on_voice_final(t)),
            on_state=lambda on: self.broadcast_ts({"type": "stt", "state": "listening" if on else "off"}),
            on_error=lambda m: self.send_error_ts(m),
        )

    # ------------------------------------------------------------------
    # Strings
    # ------------------------------------------------------------------

    def t(self, key: str) -> str:
        return LOG_STRINGS[self.lang][key]

    # ------------------------------------------------------------------
    # Async plumbing
    # ------------------------------------------------------------------

    def _run_coro(self, coro) -> None:
        """Schedule a coroutine from any thread."""
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def broadcast_ts(self, message: dict) -> None:
        """Thread-safe broadcast."""
        self._run_coro(self.broadcast(message))

    def send_error_ts(self, message: str) -> None:
        self.broadcast_ts({"type": "error", "message": message})

    async def broadcast(self, message: dict) -> None:
        if not self.clients:
            return
        payload = json.dumps(message, ensure_ascii=False)
        stale = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                stale.append(ws)
        for ws in stale:
            self.clients.discard(ws)

    async def log_to_ui(self, message: str, level: str = "info") -> None:
        await self.broadcast({"type": "log", "level": level, "message": message})

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def llm_ok(self) -> bool:
        """Cached Ollama reachability, refreshed at most every 10 s."""
        if time.time() - self._llm_checked > 10:
            self._llm_checked = time.time()
            self._llm_ok = self.llm.is_reachable()
        return self._llm_ok

    def status(self) -> dict:
        return {
            "type": "status",
            "connected": self.robot.connected,
            "robot_available": self.robot.available,
            "battery": self.robot.battery_voltage(),
            "camera": self.robot.camera_enabled,
            "emotion": self.emotions.info(),
            "emotions": emotion_list(),
            "stt_available": self._stt_available,
            "stt_listening": self.stt.listening if self.stt else False,
            "llm_ok": self._llm_ok,
            "llm_model": self.llm.model,
            "pet_mode": self.pet.running,
            "memory_count": self.memory.count(),
            "recording": self.recorder.recording,
            "faces_available": self._faces_available,
            "faces_enabled": self.faces_enabled and self._faces_available,
            "wake_mode": self.wake_mode,
            "personality": self.llm.personality,
            "personality_display": self.llm.personality_display(self.lang),
            "personalities": self.llm.list_personalities(self.lang),
            "lang": self.lang,
            "version": __version__,
        }

    # ------------------------------------------------------------------
    # Emotion expression
    # ------------------------------------------------------------------

    def _on_emotion_change(self, new: str, old: str) -> None:
        """Called (from any thread) when the mood changes."""
        log.info("Emotion: %s -> %s", old, new)
        self.broadcast_ts(self.status())
        if self.robot.connected:
            def express() -> None:
                try:
                    self.robot.set_lights(self.emotions.color())
                    self.robot.play_anim(self.emotions.animation())
                except Exception:  # noqa: BLE001
                    pass
            self.robot.submit(express)

    # ------------------------------------------------------------------
    # Chat / LLM
    # ------------------------------------------------------------------

    async def handle_chat(self, text: str, speak: bool = True) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.emotions.interact()
        self.emotions.detect_from_text(text)
        self.memory.add(KIND_USER, text, lang=self.lang, emotion=self.emotions.get())
        if not self.llm_ok():
            await self.log_to_ui(self.t("llm_down"), "warn")
            return
        context = self.memory.conversation_context(turns=6)
        # 检测用户输入语言：含中文字符用 zh，否则用 config.language
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)
        input_lang = "zh" if has_chinese else self.lang
        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                lambda: self.llm.chat(
                    text,
                    emotion_modifier=self.emotions.modifier(input_lang),
                    context=context,
                    lang=input_lang,
                ),
            )
        except RuntimeError as e:
            await self.log_to_ui(str(e), "error")
            return
        reply = (reply or "").strip()
        if not reply:
            return
        self.memory.add(KIND_COZMO, reply, lang=self.lang, emotion=self.emotions.get())
        await self.broadcast({"type": "chat", "role": "cozmo", "text": reply})
        if speak and self.robot.connected:
            self.robot.submit(self._safe(self.robot.say, reply, self.lang))
            short = reply[:60] if len(reply) > 60 else reply
            self.robot.submit(self._safe(self.robot.display_text, short, 8.0))

    # ------------------------------------------------------------------
    # Voice (STT callbacks run on the Vosk thread)
    # ------------------------------------------------------------------

    async def _on_voice_final(self, text: str) -> None:
        await self.broadcast({"type": "chat", "role": "user", "text": text, "via": "voice"})
        cmd = parse(text)
        if cmd:
            await self.log_to_ui(f"🎤 {cmd.describe(self.lang)}")
            await self.execute_command(cmd)
        else:
            await self.handle_chat(text)

    async def _on_wake_utterance(self, text: str) -> None:
        """Continuous listening: only react when the wake word is present."""
        lower = text.lower()
        wake = next((w for w in WAKE_WORDS if w in lower), None)
        if wake is None:
            return
        self.emotions.interact()
        remainder = lower.split(wake, 1)[1].strip(" ,.!?¿¡")
        if not remainder:
            ack = random.choice(WAKE_ACKS[self.lang])
            await self.broadcast({"type": "chat", "role": "cozmo", "text": ack})
            if self.robot.connected:
                self.robot.submit(self._safe(self.robot.say, ack))
            return
        await self.broadcast({"type": "chat", "role": "user", "text": remainder, "via": "voice"})
        cmd = parse(remainder)
        if cmd:
            await self.log_to_ui(f"🗣️ {cmd.describe(self.lang)}")
            await self.execute_command(cmd)
        else:
            await self.handle_chat(remainder)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    @staticmethod
    def _safe(fn, *args):
        """Wrap a robot call so failures are logged, not raised on the robot thread."""
        def wrapper() -> None:
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                log.warning("Robot action failed: %s", e)
        return wrapper

    def _require_robot(self) -> bool:
        if not self.robot.connected:
            self.broadcast_ts({"type": "error", "message": self.t("need_robot")})
            return False
        return True

    async def execute_command(self, cmd: Command) -> None:
        self.emotions.interact()
        self.memory.add(KIND_COMMAND, cmd.raw or cmd.action, lang=self.lang, emotion=self.emotions.get())
        r = self.robot

        if cmd.action == "mood":
            emotion = self.emotions.get()
            name = EMOTION_NAMES[emotion][self.lang]
            phrase = f"Estoy {name}" if self.lang == "es" else f"I feel {name}"
            await self.broadcast({"type": "chat", "role": "cozmo", "text": phrase})
            if r.connected:
                r.submit(self._safe(r.say, phrase))
            return

        if cmd.action in ("happy", "sad"):
            self.emotions.set(cmd.action, reason="user command")
            return

        if cmd.action == "sleep":
            self.emotions.set("tired", reason="user command")
            return

        if not self._require_robot():
            return

        if cmd.action == "forward":
            secs = min(max(cmd.number or 1.0, 0.3), 3.0)
            r.submit(self._safe(r.drive, 100.0, 100.0, secs))
        elif cmd.action == "backward":
            secs = min(max(cmd.number or 1.0, 0.3), 3.0)
            r.submit(self._safe(r.drive, -100.0, -100.0, secs))
        elif cmd.action == "left":
            r.submit(self._safe(r.turn, cmd.number or 90.0))
        elif cmd.action == "right":
            r.submit(self._safe(r.turn, -(cmd.number or 90.0)))
        elif cmd.action == "dance":
            r.submit(self._safe(r.dance))
        elif cmd.action == "look":
            r.submit(self._safe(r.look_around))
        elif cmd.action == "lift_up":
            r.submit(self._safe(r.move_lift, 1.0))
        elif cmd.action == "lift_down":
            r.submit(self._safe(r.move_lift, 0.0))
        elif cmd.action == "head_up":
            r.submit(self._safe(r.move_head, 1.0))
        elif cmd.action == "head_down":
            r.submit(self._safe(r.move_head, 0.0))
        elif cmd.action == "say":
            r.submit(self._safe(r.say, cmd.text))
        elif cmd.action == "lights":
            if cmd.color in COLORS:
                r.submit(self._safe(r.set_lights, COLORS[cmd.color]))
        elif cmd.action == "photo":
            r.submit(self._take_photo)

    def _take_photo(self) -> None:
        """Runs on the robot thread; saves and broadcasts the photo."""
        try:
            self.config.photos_dir.mkdir(parents=True, exist_ok=True)
            filename = f"cozmo_{int(time.time())}.jpg"
            data = self.robot.save_photo(self.config.photos_dir / filename)
            if data:
                self.broadcast_ts({
                    "type": "photo",
                    "image": base64.b64encode(data).decode(),
                    "saved": filename,
                })
            else:
                self.send_error_ts("camera" if self.lang == "en" else "sin imagen de cámara")
        except Exception as e:  # noqa: BLE001
            self.send_error_ts(str(e))

    # ------------------------------------------------------------------
    # WebSocket message dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, msg: dict, ws: WebSocket) -> None:
        action = msg.get("action", "")
        r = self.robot

        if action == "ping":
            await ws.send_text(json.dumps({"type": "pong"}))
        elif action == "get_status":
            await ws.send_text(json.dumps(self.status(), ensure_ascii=False))
        elif action == "connect":
            await self.log_to_ui(self.t("connecting"))
            r.connect(on_done=lambda ok, m: self._run_coro(self._on_connect_done(ok, m)))
        elif action == "disconnect":
            self._reconnecting = False
            r.disconnect()
            await self.log_to_ui(self.t("disconnected"))
        elif action == "drive":
            if self._require_robot():
                r.submit(self._safe(r.drive, msg.get("left", 0), msg.get("right", 0), msg.get("duration", 0.3)))
        elif action == "stop":
            if r.connected:
                r.submit(self._safe(r.stop))
        elif action == "lift":
            if self._require_robot():
                r.submit(self._safe(r.move_lift, msg.get("value", 0.5)))
        elif action == "head":
            if self._require_robot():
                r.submit(self._safe(r.move_head, msg.get("value", 0.5)))
        elif action == "say":
            if self._require_robot():
                text = str(msg.get("text", ""))[:200]
                r.submit(self._safe(r.say, text))
        elif action == "anim":
            if self._require_robot():
                r.submit(self._safe(r.play_anim, str(msg.get("name", ""))))
        elif action == "dance":
            if self._require_robot():
                r.submit(self._safe(r.dance))
        elif action == "look":
            if self._require_robot():
                r.submit(self._safe(r.look_around))
        elif action == "lights":
            if self._require_robot():
                color = msg.get("color", "off")
                rgb = COLORS.get(color) or self._parse_hex_color(color)
                if rgb:
                    r.submit(self._safe(r.set_lights, rgb))
        elif action == "camera":
            enabled = bool(msg.get("enabled"))
            if enabled and self._require_robot():
                r.submit(self._safe(r.set_camera, True))
            elif not enabled and r.connected:
                r.submit(self._safe(r.set_camera, False))
        elif action == "photo":
            if self._require_robot():
                r.submit(self._take_photo)
        elif action == "emotion":
            self.emotions.set(str(msg.get("name", "")), reason="user")
        elif action == "chat":
            text = str(msg.get("text", ""))
            await self.broadcast({"type": "chat", "role": "user", "text": text})
            await self.handle_chat(text)
        elif action == "command":
            text = str(msg.get("text", ""))
            cmd = parse(text)
            if cmd:
                await self.log_to_ui(f"⌨️ {cmd.describe(self.lang)}")
                await self.execute_command(cmd)
            else:
                await self.log_to_ui(f"{self.t('cmd_not_recognized')}: «{text}»", "warn")
        elif action == "stt":
            self._toggle_stt(bool(msg.get("enabled", True)))
        elif action == "wake":
            self._toggle_wake(bool(msg.get("enabled", True)))
        elif action == "faces":
            self._toggle_faces(bool(msg.get("enabled", True)))
        elif action == "record":
            if bool(msg.get("enabled")):
                self.recorder.start()
                await self.log_to_ui(self.t("rec_on"), "ok")
                await self.broadcast(self.status())
            else:
                loop = asyncio.get_running_loop()
                path = await loop.run_in_executor(None, self._stop_recording)
                if path:
                    await self.log_to_ui(f"{self.t('rec_saved')}: {path.name}", "ok")
                    await self.broadcast({"type": "recording_saved", "file": path.name,
                                          "url": f"/recordings/{path.name}"})
                else:
                    await self.log_to_ui(self.t("rec_empty"), "warn")
                await self.broadcast(self.status())
        elif action == "pet":
            self._toggle_pet(bool(msg.get("enabled", True)))
        elif action == "memory_clear":
            self.memory.clear()
            self.memory.add(KIND_EVENT, "memory cleared", lang=self.lang)
            await self.log_to_ui(self.t("memory_cleared"), "ok")
            await self.broadcast(self.status())
        elif action == "personality":
            name = str(msg.get("name", ""))
            if self.llm.set_personality(name):
                await self.log_to_ui(f"🎭 {self.llm.personality_display(self.lang)}")
                await self.broadcast(self.status())
        elif action == "lang":
            lang = str(msg.get("lang", ""))
            if lang in LOG_STRINGS:
                self.lang = lang
                self.pet.lang = lang
                await self.broadcast(self.status())
        else:
            await ws.send_text(json.dumps({"type": "error", "message": f"unknown action: {action}"}))

    @staticmethod
    def _parse_hex_color(value: str):
        value = value.lstrip("#")
        if len(value) == 6:
            try:
                return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                return None
        return None

    def _toggle_stt(self, enabled: bool) -> None:
        if not self.stt:
            self.broadcast_ts({"type": "error", "message": self.t("stt_unavailable")})
            return
        if enabled and self.wake_mode:
            self.broadcast_ts({"type": "log", "level": "warn", "message": self.t("wake_busy")})
            return
        if enabled:
            self.stt.start_listening()
        else:
            self.stt.stop_listening()

    def _toggle_wake(self, enabled: bool) -> None:
        if not self._stt_available:
            self.broadcast_ts({"type": "error", "message": self.t("stt_unavailable")})
            return
        if enabled:
            if self.stt and self.stt.listening:
                self.stt.stop_listening()
            if self.stt_wake is None:
                self.stt_wake = self._make_listener(continuous=True)
            if self.stt_wake:
                self.stt_wake.start_listening()
            self.wake_mode = True
        else:
            if self.stt_wake:
                self.stt_wake.stop_listening()
            self.wake_mode = False
        self.broadcast_ts({
            "type": "log",
            "level": "ok" if enabled else "info",
            "message": self.t("wake_on") if enabled else self.t("wake_off"),
        })
        self.broadcast_ts(self.status())

    def _toggle_faces(self, enabled: bool) -> None:
        if enabled and not self._faces_available:
            self.broadcast_ts({"type": "error", "message": "opencv-python-headless no instalado"})
            return
        self.faces_enabled = enabled
        if not enabled:
            self._face_present = False
            self._face_absent_checks = 0
        self.broadcast_ts({
            "type": "log",
            "level": "ok" if enabled else "info",
            "message": self.t("faces_on") if enabled else self.t("faces_off"),
        })
        self.broadcast_ts(self.status())

    def _stop_recording(self) -> Optional[Path]:
        """Runs in an executor; encodes the collected frames."""
        filename = f"cozmo_{int(time.time())}"
        return self.recorder.stop(self.config.recordings_dir / filename)

    def _toggle_pet(self, enabled: bool) -> None:
        if enabled:
            self.pet.start()
            self.broadcast_ts({"type": "log", "level": "ok", "message": self.t("pet_on")})
        else:
            self.pet.stop()
            self.broadcast_ts({"type": "log", "level": "info", "message": self.t("pet_off")})
        self.broadcast_ts(self.status())

    async def _on_connect_done(self, ok: bool, message: str) -> None:
        self._reconnecting = False
        if ok:
            await self.log_to_ui(self.t("connected"), "ok")
            self.emotions.set("happy", reason="connected")
        else:
            await self.log_to_ui(f"{self.t('connect_failed')} ({message})", "error")
        await self.broadcast(self.status())

    def _start_reconnect(self, reason: str) -> None:
        """Kick off an automatic reconnect after a silent session death."""
        if self._reconnecting:
            return
        self._reconnecting = True
        self.broadcast_ts({"type": "log", "level": "warn",
                           "message": f"Robot sin respuesta ({reason}); reconectando..."})
        def on_done(ok: bool, msg: str) -> None:
            self._run_coro(self._on_reconnect_done(ok, msg))
        self.robot.reconnect(on_done=on_done)

    async def _on_reconnect_done(self, ok: bool, msg: str) -> None:
        self._reconnecting = False
        if ok:
            await self.log_to_ui("Robot reconectado", "ok")
        else:
            await self.log_to_ui(f"Reconexión fallida ({msg}); reintentaré", "warn")
        await self.broadcast(self.status())

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _camera_loop(self) -> None:
        interval = 1.0 / max(self.config.camera_fps, 1.0)
        face_future = None
        while True:
            try:
                # TTS 播放期间暂停摄像头广播：JPEG 帧（~50-200KB）+ 音频包
                # 同时传输会占满 WiFi 上行，导致 RobotState 延迟 30s+ 触发断开
                if self.robot._tts_active:
                    await asyncio.sleep(interval)
                    continue
                if self.clients and self.robot.connected and self.robot.camera_enabled:
                    data = self.robot.latest_jpeg(self.config.camera_quality)
                    if data:
                        await self.broadcast({
                            "type": "camera",
                            "image": base64.b64encode(data).decode(),
                        })
                        if self.recorder.recording:
                            self.recorder.add_frame(data)
                        if self.faces_enabled and self._face_detector:
                            if face_future is not None and face_future.done():
                                faces, (width, _h) = face_future.result()
                                face_future = None
                                await self._on_faces(faces, width)
                            if face_future is None:
                                loop = asyncio.get_running_loop()
                                face_future = loop.run_in_executor(
                                    None, self._face_detector.detect_jpeg, data
                                )
            except Exception:  # noqa: BLE001
                log.exception("camera loop error")
            await asyncio.sleep(interval)

    async def _on_faces(self, faces, width: int) -> None:
        """React to face presence changes (with hysteresis against flicker)."""
        if faces:
            self._face_absent_checks = 0
            if not self._face_present:
                self._face_present = True
                await self.log_to_ui(self.t("face_seen"), "ok")
                self.memory.add(KIND_EVENT, "vi una cara", lang=self.lang)
                self.emotions.set("excited", reason="face seen")
                angle = turn_angle_for_face(faces[0], width)
                if angle is not None and self.robot.connected:
                    self.robot.submit(self._safe(self.robot.turn, angle))
        else:
            self._face_absent_checks += 1
            if self._face_absent_checks >= 4:
                self._face_present = False

    async def _status_loop(self) -> None:
        while True:
            try:
                # Refresh Ollama reachability off the event loop.
                if time.time() - self._llm_checked > 10:
                    loop = asyncio.get_running_loop()
                    self._llm_ok = await loop.run_in_executor(None, self.llm.is_reachable)
                    self._llm_checked = time.time()
                self.emotions.tick(dt=self.config.status_interval)
                problem = self.robot.health()
                if problem:
                    self._start_reconnect(problem)
                await self.broadcast(self.status())
            except Exception:  # noqa: BLE001
                log.exception("status loop error")
            await asyncio.sleep(self.config.status_interval)


def create_app(config: Config) -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="Cozmo Companion", version=__version__)
    hub = Hub(config)
    app.state.hub = hub
    # Must exist before the StaticFiles mounts below.
    config.recordings_dir.mkdir(parents=True, exist_ok=True)

    @app.on_event("startup")
    async def _startup() -> None:
        hub.loop = asyncio.get_running_loop()
        config.photos_dir.mkdir(parents=True, exist_ok=True)
        config.recordings_dir.mkdir(parents=True, exist_ok=True)
        hub._llm_ok = hub.llm.is_reachable()
        hub._llm_checked = time.time()
        hub._tasks = [
            asyncio.create_task(hub._camera_loop()),
            asyncio.create_task(hub._status_loop()),
        ]
        log.info("Cozmo Companion %s ready (STT=%s, LLM=%s)",
                 __version__, hub._stt_available, hub._llm_ok)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        for task in hub._tasks:
            task.cancel()
        if hub.stt:
            hub.stt.stop_listening()
        if hub.stt_wake:
            hub.stt_wake.stop_listening()
        hub.pet.stop()
        hub.robot.shutdown()
        hub.memory.close()

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/recordings")
    async def recordings():
        files = sorted(
            (p.name for p in config.recordings_dir.glob("*.*") if p.suffix in (".mp4", ".gif")),
            reverse=True,
        )
        return {"files": files}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        hub.clients.add(ws)
        try:
            await ws.send_text(json.dumps(hub.status(), ensure_ascii=False))
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await hub.dispatch(msg, ws)
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/recordings", StaticFiles(directory=config.recordings_dir), name="recordings")
    return app
