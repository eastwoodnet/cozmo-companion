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

import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Tuple

log = logging.getLogger("companion.robot")

try:
    import pycozmo

    HAS_PYCOZMO = True
except ImportError:
    pycozmo = None  # type: ignore
    HAS_PYCOZMO = False

# Calibrated in Cozmo Voice Commands: at wheel speed 100 Cozmo turns ~130 deg/s.
TURN_DEG_PER_SEC = 130.0
MAX_DRIVE_DURATION = 3.0
SPEAK_SECS_PER_CHAR = 0.08


class Robot:
    """High-level, thread-safe interface to a physical Cozmo via pycozmo."""

    def __init__(self) -> None:
        self._client = None
        self._connected = False
        self._lock = threading.RLock()
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cozmo")
        self._camera_enabled = False

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
        self._exec.submit(self._connect_blocking, on_done)

    def _connect_blocking(self, on_done: Optional[Callable[[bool, str], None]]) -> None:
        ok, msg = False, ""
        try:
            if not HAS_PYCOZMO:
                raise RuntimeError("pycozmo no está instalado (pip install pycozmo)")
            log.info("Connecting to Cozmo over WiFi...")
            client = pycozmo.Client()
            client.start()
            client.connect()
            try:
                client.wait_for_robot(timeout=10.0)
            except TypeError:  # older pycozmo without timeout arg
                client.wait_for_robot()
            with self._lock:
                self._client = client
                self._connected = True
            ok, msg = True, "connected"
            log.info("Connected to Cozmo")
        except Exception as e:  # noqa: BLE001 - report any failure to the UI
            msg = str(e) or e.__class__.__name__
            log.warning("Connection failed: %s", msg)
        if on_done:
            try:
                on_done(ok, msg)
            except Exception:  # noqa: BLE001
                log.exception("connect callback failed")

    def disconnect(self) -> None:
        self._exec.submit(self._disconnect_blocking)

    def _disconnect_blocking(self) -> None:
        with self._lock:
            client, self._client = self._client, None
            self._connected = False
            self._camera_enabled = False
        if client is not None:
            for method in (client.disconnect, client.stop):
                try:
                    method()
                except Exception:  # noqa: BLE001
                    pass

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
        """Drive wheels at given speeds for a clamped duration."""
        client = self._get_client()
        duration = self._clamp(float(duration), 0.05, MAX_DRIVE_DURATION)
        client.drive_wheels(lwheel_speed=float(left), rwheel_speed=float(right), duration=duration)

    def stop(self) -> None:
        client = self._get_client()
        client.drive_wheels(lwheel_speed=0.0, rwheel_speed=0.0, duration=0.1)

    def turn(self, degrees: float) -> None:
        """Turn in place; positive = left, negative = right."""
        speed = 100.0
        duration = self._clamp(abs(degrees) / TURN_DEG_PER_SEC, 0.1, 2.0)
        left = speed if degrees > 0 else -speed
        self.drive(left, -left, duration)
        time.sleep(duration + 0.05)

    def move_lift(self, value: float) -> None:
        """Move lift, 0.0 (down) to 1.0 (up)."""
        self._get_client().move_lift(self._clamp(float(value), 0.0, 1.0))

    def move_head(self, value: float) -> None:
        """Move head, 0.0 (down) to 1.0 (up)."""
        self._get_client().move_head(self._clamp(float(value), 0.0, 1.0))

    # ------------------------------------------------------------------
    # Speech / animations / lights
    # ------------------------------------------------------------------

    def say(self, text: str) -> None:
        """Speak text with Cozmo's TTS voice (blocks for speech duration)."""
        text = (text or "").strip()
        if not text:
            return
        client = self._get_client()
        client.say_text(text)
        time.sleep(max(1.0, len(text) * SPEAK_SECS_PER_CHAR))

    def play_anim(self, name: str) -> None:
        """Play a named animation; tolerates unknown names."""
        client = self._get_client()
        try:
            client.play_anim(name)
            time.sleep(2.0)
        except Exception as e:  # noqa: BLE001
            log.warning("Animation %s failed: %s", name, e)
            time.sleep(0.5)

    def set_lights(self, rgb: Tuple[int, int, int]) -> None:
        client = self._get_client()
        light = pycozmo.lights.Light(pycozmo.lights.Color(int(rgb[0]), int(rgb[1]), int(rgb[2])))
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
        for _ in range(3):
            client.drive_wheels(lwheel_speed=100.0, rwheel_speed=-100.0, duration=0.3)
            time.sleep(0.15)
            client.drive_wheels(lwheel_speed=-100.0, rwheel_speed=100.0, duration=0.3)
            time.sleep(0.15)
        try:
            client.move_lift(1.0)
            time.sleep(0.4)
            client.move_lift(0.0)
        except Exception:  # noqa: BLE001
            pass

    def look_around(self) -> None:
        """Scan the surroundings turning left and right."""
        for angle in (35, -70, 35, -30):
            self.turn(float(angle))
            time.sleep(0.2)

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    def set_camera(self, enabled: bool) -> None:
        client = self._get_client()
        if enabled:
            client.camera.start()
        else:
            client.camera.stop()
        with self._lock:
            self._camera_enabled = bool(enabled)

    def latest_jpeg(self, quality: int = 60) -> Optional[bytes]:
        """Return the latest camera frame as JPEG bytes, or None."""
        try:
            with self._lock:
                client = self._client
            if client is None or not getattr(client, "camera", None):
                return None
            img = client.camera.latest_image
            if img is None or not hasattr(img, "raw_image"):
                return None
            buf = io.BytesIO()
            img.raw_image.save(buf, format="JPEG", quality=quality)
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
