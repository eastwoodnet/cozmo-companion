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
from .llm import OllamaClient
from .memory import KIND_COMMAND, KIND_COZMO, KIND_EVENT, KIND_USER, Memory
from .pet_mode import PetMode
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
    },
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
        self.llm = OllamaClient(config.ollama_url, config.ollama_model)
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
        self.stt: Optional[VoskListener] = None
        if self._stt_available:
            self.stt = VoskListener(
                config.vosk_model,
                on_partial=lambda t: self.broadcast_ts({"type": "stt", "state": "listening", "partial": t}),
                on_final=lambda t: self.broadcast_ts({"type": "stt", "state": "off", "final": t})
                and self._run_coro(self._on_voice_final(t)),
                on_state=lambda on: self.broadcast_ts({"type": "stt", "state": "listening" if on else "off"}),
                on_error=lambda m: self.send_error_ts(m),
            )

        self._llm_ok = False
        self._llm_checked = 0.0
        self._tasks = []

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
        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                lambda: self.llm.chat(
                    text,
                    emotion_modifier=self.emotions.modifier(self.lang),
                    context=context,
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
            self.robot.submit(self._safe(self.robot.say, reply))

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
        if enabled:
            self.stt.start_listening()
        else:
            self.stt.stop_listening()

    def _toggle_pet(self, enabled: bool) -> None:
        if enabled:
            self.pet.start()
            self.broadcast_ts({"type": "log", "level": "ok", "message": self.t("pet_on")})
        else:
            self.pet.stop()
            self.broadcast_ts({"type": "log", "level": "info", "message": self.t("pet_off")})
        self.broadcast_ts(self.status())

    async def _on_connect_done(self, ok: bool, message: str) -> None:
        if ok:
            await self.log_to_ui(self.t("connected"), "ok")
            self.emotions.set("happy", reason="connected")
        else:
            await self.log_to_ui(f"{self.t('connect_failed')} ({message})", "error")
        await self.broadcast(self.status())

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _camera_loop(self) -> None:
        interval = 1.0 / max(self.config.camera_fps, 1.0)
        while True:
            try:
                if self.clients and self.robot.connected and self.robot.camera_enabled:
                    data = self.robot.latest_jpeg(self.config.camera_quality)
                    if data:
                        await self.broadcast({
                            "type": "camera",
                            "image": base64.b64encode(data).decode(),
                        })
            except Exception:  # noqa: BLE001
                log.exception("camera loop error")
            await asyncio.sleep(interval)

    async def _status_loop(self) -> None:
        while True:
            try:
                # Refresh Ollama reachability off the event loop.
                if time.time() - self._llm_checked > 10:
                    loop = asyncio.get_running_loop()
                    self._llm_ok = await loop.run_in_executor(None, self.llm.is_reachable)
                    self._llm_checked = time.time()
                self.emotions.tick(dt=self.config.status_interval)
                await self.broadcast(self.status())
            except Exception:  # noqa: BLE001
                log.exception("status loop error")
            await asyncio.sleep(self.config.status_interval)


def create_app(config: Config) -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="Cozmo Companion", version=__version__)
    hub = Hub(config)
    app.state.hub = hub

    @app.on_event("startup")
    async def _startup() -> None:
        hub.loop = asyncio.get_running_loop()
        config.photos_dir.mkdir(parents=True, exist_ok=True)
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
        hub.pet.stop()
        hub.robot.shutdown()
        hub.memory.close()

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

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
    return app
