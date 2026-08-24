"""Thread-safe pycozmo wrapper for Cozmo Companion.

All robot actions are serialized through a single-thread executor so commands
never overlap on the wire. pycozmo is imported lazily: the server can run
(UI, chat, STT) even when pycozmo or the robot is unavailable.

Connection protocol (from pycozmo 0.8):
    client = pycozmo.Client()
    client.start()
    client.connect()
    client.wait_for_robot(timeout=...)
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional, Tuple

log = logging.getLogger("companion.robot")

# pycozmo 内置 ping 依赖接收线程，动画/显示包繁忙时 ping 延迟，
# Cozmo 5s 没收到 ping 就断开。自己发 ping 保证连接不断。
_PING_INTERVAL = 0.5
_ping_stop = threading.Event()
_ping_thread: Optional[threading.Thread] = None


def _ping_loop(client) -> None:
    """每 0.5s 发一个 Ping 包，保持 Cozmo 连接活跃。"""
    import time as _time
    counter = 0
    while not _ping_stop.is_set():
        try:
            conn = client.conn
            if conn is not None:
                import pycozmo.protocol_encoder as pe
                pkt = pe.Ping(_time.perf_counter(), counter, 0)
                conn.send(pkt)
                counter += 1
        except Exception:  # noqa: BLE001
            pass
        _ping_stop.wait(_PING_INTERVAL)


def _ensure_chunk_module() -> None:
    """Ensure `chunk` is importable before importing pycozmo.

    Python 3.13 removed the `chunk` stdlib module, but pycozmo 0.8.0 imports
    it at package level (audiokinetic.soundbank). Register the vendored copy
    (companion/_chunk.py) in sys.modules when the stdlib one is missing.
    """
    try:
        importlib.import_module("chunk")
        return
    except ImportError:
        pass
    spec = importlib.util.spec_from_file_location(
        "chunk", Path(__file__).with_name("_chunk.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["chunk"] = module


PYCOZMO_IMPORT_ERROR: Optional[str] = None

try:
    _ensure_chunk_module()
    import pycozmo

    HAS_PYCOZMO = True
except ImportError as e:
    pycozmo = None  # type: ignore
    HAS_PYCOZMO = False
    PYCOZMO_IMPORT_ERROR = f"{e.__class__.__name__}: {e}"

# Calibrated in Cozmo Voice Commands: at wheel speed 100 Cozmo turns ~130 deg/s.
TURN_DEG_PER_SEC = 130.0
MAX_DRIVE_DURATION = 3.0

# El robot despierto emite ~30 RobotState/s; sin paquetes en este intervalo
# la sesión está muerta (robot dormido, Disconnect, WiFi caído).
# 30s: Cozmo 在动画/显示包繁忙时 ping 可能被延迟，5-10s 太敏感导致频繁重连。
STALE_STATE_SECS = 30.0

# pycozmo.conn.Connection.CONNECTED (3); el robot puede cerrar la sesión
# enviando Disconnect y el estado pasa a IDLE sin avisar.
try:
    from pycozmo.conn import Connection as _PycozmoConnection

    _CONNECTED_STATE = _PycozmoConnection.CONNECTED
except Exception:  # noqa: BLE001 - pycozmo no instalado o versión distinta
    _CONNECTED_STATE = 3

# Hotspot del robot (SSID único por Cozmo). Si wlan0 lo pierde y el robot
# vuelve a despertar, se reintenta la conexión WiFi con nmcli.
WIFI_SSID = os.environ.get("COZMO_WIFI_SSID", "")
WIFI_PASSWORD = os.environ.get("COZMO_WIFI_PASSWORD", "")
_WIFI_REJOIN_COOLDOWN_SECS = 60.0
_last_wifi_attempt = 0.0

# Cozmo 触发悬崖后固件锁死驱动方向，软件无法让它后退。
# 防护：cliff bit 设位时停止发送 drive 命令，让 Cozmo 停在边缘。
CLIFF_STATUS_BIT = 0x4000


class Robot:
    """High-level, thread-safe interface to a physical Cozmo via pycozmo."""

    def __init__(self) -> None:
        self._client = None
        self._connected = False
        self._lock = threading.RLock()
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cozmo")
        self._camera_enabled = False
        self._last_state_ts = 0.0
        self._auto_reconnect = False
        self._connecting = False
        self._cliff_locked = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True if the pycozmo package is installed."""
        return HAS_PYCOZMO

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def camera_enabled(self) -> bool:
        with self._lock:
            return self._camera_enabled

    def submit(self, fn: Callable, *args, **kwargs):
        """Queue an action on the robot thread."""
        return self._exec.submit(fn, *args, **kwargs)

    def connect(self, on_done: Optional[Callable[[bool, str], None]] = None) -> None:
        """Connect to Cozmo in the background; calls on_done(ok, message)."""
        with self._lock:
            if self._connected:
                if on_done:
                    on_done(True, "ya conectado")
                return
            if self._connecting:
                if on_done:
                    on_done(False, "conectando")
                return
            self._connecting = True
        self._exec.submit(self._connect_blocking, on_done)

    def _connect_blocking(self, on_done: Optional[Callable[[bool, str], None]]) -> None:
        ok, msg = False, ""
        try:
            if not HAS_PYCOZMO:
                msg = "pycozmo no está instalado (pip install pycozmo)"
                if PYCOZMO_IMPORT_ERROR:
                    msg += f" — error de importación: {PYCOZMO_IMPORT_ERROR}"
                raise RuntimeError(msg)
            log.info("Connecting to Cozmo over WiFi...")
            self._teardown_client()
            self._ensure_wifi()
            client = pycozmo.Client()
            client.start()
            client.connect()
            try:
                client.wait_for_robot(timeout=10.0)
            except TypeError:  # older pycozmo without timeout arg
                client.wait_for_robot()
            # Vigilancia de salud: cada RobotState refresca el timestamp.
            client.add_handler(pycozmo.protocol_encoder.RobotState, self._on_robot_state_tick)
            # 禁用动画控制器：每帧(30fps)发 DisplayImage+OutputSilence 导致
            # SendThread 队列塞满，ping 被延迟，Cozmo 30s 无响应就断开。
            # 需要显示表情/图片时临时 enable_animations(True)。
            client.enable_animations(False)
            client.enable_procedural_face(False)
            with self._lock:
                self._client = client
                self._connected = True
                self._camera_enabled = False
                self._last_state_ts = time.monotonic()
                self._auto_reconnect = True
            # 启动独立 ping 线程，防止 pycozmo 内置 ping 延迟导致断开
            global _ping_thread
            _ping_stop.clear()
            _ping_thread = threading.Thread(target=_ping_loop, args=(client,), daemon=True)
            _ping_thread.start()
            ok, msg = True, "connected"
            log.info("Connected to Cozmo")
            # load_anims 在 aarch64 上很慢（find_file 对每个 pair 都 os.walk），
            # 放后台线程避免阻塞 connect 完成。
            def _bg_load_anims():
                try:
                    client.load_anims()
                    log.info("Animaciones cargadas")
                except Exception as e:  # noqa: BLE001
                    log.warning("Animaciones no disponibles: %s", e)
            threading.Thread(target=_bg_load_anims, daemon=True).start()
        except Exception as e:  # noqa: BLE001 - report any failure to the UI
            msg = str(e) or e.__class__.__name__
            log.warning("Connection failed: %s", msg)
        finally:
            with self._lock:
                self._connecting = False
        if on_done:
            try:
                on_done(ok, msg)
            except Exception:  # noqa: BLE001
                log.exception("connect callback failed")

    def disconnect(self) -> None:
        self._exec.submit(self._disconnect_blocking)

    def _disconnect_blocking(self) -> None:
        with self._lock:
            self._auto_reconnect = False
        self._teardown_client()

    def _teardown_client(self) -> None:
        """Stop the current client (if any) and clear the connection state."""
        global _ping_thread
        _ping_stop.set()
        if _ping_thread is not None:
            _ping_thread.join(timeout=2.0)
            _ping_thread = None
        with self._lock:
            client, self._client = self._client, None
            self._connected = False
            self._camera_enabled = False
            self._last_state_ts = 0.0
            self._cliff_locked = False
        if client is not None:
            for method in (client.disconnect, client.stop):
                try:
                    method()
                except Exception:  # noqa: BLE001
                    pass

    def reconnect(self, on_done: Optional[Callable[[bool, str], None]] = None) -> None:
        """Tear down and connect again (used by the server health loop)."""
        self._exec.submit(self._connect_blocking, on_done)

    def health(self) -> Optional[str]:
        """None si está sano o simplemente desconectado; si no, el problema.

        Detecta la muerte silenciosa de la sesión: el robot puede dejar de
        responder (se duerme, envía Disconnect, cae la WiFi) mientras
        ``connected`` sigue siendo True y todos los comandos se pierden.
        """
        with self._lock:
            client = self._client
            connected = self._connected
            last = self._last_state_ts
            auto = self._auto_reconnect
        if connected and client is not None:
            state = getattr(getattr(client, "conn", None), "state", None)
            if state is not None and state != _CONNECTED_STATE:
                return "sesión cerrada por el robot (Disconnect)"
            if last and time.monotonic() - last > STALE_STATE_SECS:
                return f"sin RobotState desde hace {time.monotonic() - last:.0f}s"
            return None
        if auto:
            return "desconectado"
        return None

    def _on_robot_state_tick(self, cli, pkt) -> None:
        self._last_state_ts = time.monotonic()
        status = getattr(pkt, "status", 0) or 0
        cliff = bool(status & CLIFF_STATUS_BIT)
        if cliff and not self._cliff_locked:
            self._cliff_locked = True
            log.warning("Acantilado detectado (status=%d); bloqueando drive", status)
            try:
                client = self._client
                if client is not None:
                    client.stop_all_motors()
            except Exception:  # noqa: BLE001
                pass
        elif not cliff and self._cliff_locked:
            self._cliff_locked = False
            log.info("Acantilado despejado; drive desbloqueado")

    def _ensure_wifi(self) -> None:
        """Reincorporar al hotspot del robot si wlan0 lo perdió.

        El hotspot de Cozmo solo existe con el robot despierto; al dormirse
        wlan0 queda sin la subred 172.31.1.0/24 y el UDP muere. Con cooldown
        para no escanear WiFi en bucle mientras el robot sigue dormido.
        """
        global _last_wifi_attempt
        if not (WIFI_SSID and WIFI_PASSWORD):
            return
        now = time.monotonic()
        if now - _last_wifi_attempt < _WIFI_REJOIN_COOLDOWN_SECS:
            return
        try:
            proc = subprocess.run(
                ["ip", "-4", "addr", "show", "wlan0"],
                capture_output=True, text=True, timeout=5,
            )
            if "172.31.1." in proc.stdout:
                return  # ya estamos en la subred del robot
            _last_wifi_attempt = now
            log.info("Reconectando WiFi de Cozmo %s ...", WIFI_SSID)
            subprocess.run(
                ["nmcli", "device", "wifi", "connect", WIFI_SSID,
                 "password", WIFI_PASSWORD, "ifname", "wlan0"],
                capture_output=True, timeout=30,
            )
            time.sleep(3)
        except Exception as e:  # noqa: BLE001
            log.warning("WiFi rejoin failed: %s", e)

    def shutdown(self) -> None:
        self._disconnect_blocking()
        self._exec.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        with self._lock:
            if not self._connected or self._client is None:
                raise RuntimeError("Cozmo no está conectado")
            return self._client

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    # ------------------------------------------------------------------
    # Movement (run on the robot thread via submit)
    # ------------------------------------------------------------------

    def drive(self, left: float, right: float, duration: float = 0.3) -> None:
        """Drive wheels at given speeds.

        发 DriveWheels 包，不立即 StopAllMotors。
        StopAllMotors 会导致 Cozmo 进入 status=784 状态（~25% 时间），
        不响应后续 DriveWheels 命令。让 Cozmo 自然停止。
        Cliff bit 设位时拒绝驱动，防止 Cozmo 在悬崖边继续前进。
        """
        if self._cliff_locked:
            log.warning("Drive bloqueado: acantilado")
            return
        client = self._get_client()
        client.drive_wheels(lwheel_speed=float(left), rwheel_speed=float(right))

    def stop(self) -> None:
        client = self._get_client()
        client.stop_all_motors()

    def turn(self, degrees: float) -> None:
        """Turn in place; positive = left, negative = right."""
        speed = 100.0
        duration = self._clamp(abs(degrees) / TURN_DEG_PER_SEC, 0.1, 2.0)
        left = speed if degrees > 0 else -speed
        client = self._get_client()
        client.drive_wheels(lwheel_speed=left, rwheel_speed=-left)
        time.sleep(duration)
        client.stop_all_motors()

    def move_lift(self, value: float) -> None:
        """Move lift, 0.0 (down) to 1.0 (up)."""
        v = self._clamp(float(value), 0.0, 1.0)
        height_mm = v * 70.0
        self._get_client().set_lift_height(height_mm)

    def move_head(self, value: float) -> None:
        """Move head, 0.0 (down) to 1.0 (up)."""
        v = self._clamp(float(value), 0.0, 1.0)
        angle_rad = -0.42 + v * 1.2
        self._get_client().set_head_angle(angle_rad)

    # ------------------------------------------------------------------
    # Speech / animations / lights
    # ------------------------------------------------------------------

    def say(self, text: str) -> None:
        """Speak text with Cozmo's TTS voice.

        pycozmo 0.8.0 no expone TTS (no existe say_text ni paquete SayText);
        se registra una advertencia y se descarta el texto.
        """
        text = (text or "").strip()
        if not text:
            return
        log.warning("TTS no disponible en pycozmo 0.8.0; sin decir: %s", text[:50])

    def play_anim(self, name: str) -> None:
        """Play a named animation; tolerates unknown names."""
        client = self._get_client()
        try:
            client.enable_animations(True)
            client.play_anim(name)
        except Exception as e:  # noqa: BLE001
            log.warning("Animation %s failed: %s", name, e)
        finally:
            client.enable_animations(False)

    def set_lights(self, rgb: Tuple[int, int, int]) -> None:
        client = self._get_client()
        # set_all_backpack_lights exige LightState (uint16 LED), no Color.
        color = pycozmo.lights.Color(rgb=(int(rgb[0]), int(rgb[1]), int(rgb[2])))
        light = pycozmo.protocol_encoder.LightState(
            on_color=color.to_int16(), off_color=color.to_int16())
        client.set_all_backpack_lights(light)

    def lights_off(self) -> None:
        self.set_lights((0, 0, 0))

    # ------------------------------------------------------------------
    # Composite behaviors
    # ------------------------------------------------------------------

    def dance(self) -> None:
        """Little victory dance: spin + lift."""
        client = self._get_client()
        self.set_lights((255, 80, 0))
        client.drive_wheels(lwheel_speed=100.0, rwheel_speed=-100.0)
        time.sleep(0.3)
        client.drive_wheels(lwheel_speed=-100.0, rwheel_speed=100.0)
        time.sleep(0.3)
        client.stop_all_motors()
        try:
            client.set_lift_height(70.0)
        except Exception:  # noqa: BLE001
            pass

    def look_around(self) -> None:
        """Scan the surroundings turning left and right."""
        client = self._get_client()
        for angle in (35, -70, 35, -30):
            speed = 100.0
            duration = self._clamp(abs(angle) / TURN_DEG_PER_SEC, 0.1, 2.0)
            left = speed if angle > 0 else -speed
            client.drive_wheels(lwheel_speed=left, rwheel_speed=-left)
            time.sleep(duration)
        client.stop_all_motors()

    # ------------------------------------------------------------------
    # Screen
    # ------------------------------------------------------------------

    def display_text(self, text: str, duration: float = 5.0) -> None:
        """在 Cozmo 屏幕上显示自定义文字。

        Cozmo 屏幕 128x128 单色。用 PIL 渲染文字为图片，
        临时启用动画控制器发送 DisplayImage，duration 秒后关闭。
        """
        client = self._get_client()
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            log.warning("PIL 未安装，无法显示文字")
            return
        img = Image.new("L", (128, 128), 0)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        # 简单居中
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = max(0, (128 - tw) // 2)
        y = max(0, (128 - th) // 2)
        draw.text((x, y), text, fill=255, font=font)
        client.enable_animations(True)
        client.display_image(img, duration=duration)
        client.enable_animations(False)

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    def set_camera(self, enabled: bool) -> None:
        client = self._get_client()
        client.enable_camera(bool(enabled))
        with self._lock:
            self._camera_enabled = bool(enabled)

    def latest_jpeg(self, quality: int = 60) -> Optional[bytes]:
        """Return the latest camera frame as JPEG bytes, or None."""
        try:
            with self._lock:
                client = self._client
            if client is None:
                return None
            img = getattr(client, "_latest_image", None)
            if img is None:
                return None
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return None

    def save_photo(self, path) -> Optional[bytes]:
        """Save the latest frame to disk; returns JPEG bytes or None."""
        data = self.latest_jpeg(quality=85)
        if data is None:
            return None
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError as e:
            log.warning("Could not save photo: %s", e)
        return data

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def battery_voltage(self) -> Optional[float]:
        try:
            with self._lock:
                client = self._client
            if client is None:
                return None
            voltage = getattr(client, "battery_voltage", None)
            return round(float(voltage), 2) if voltage else None
        except Exception:  # noqa: BLE001
            return None
