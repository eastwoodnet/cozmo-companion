"""Autonomous pet mode: Cozmo acts on his own based on his mood.

A background thread picks mood-appropriate actions every few seconds while
the user is idle. Any user interaction (chat, command, button) resets the
idle timer via EmotionState.interact(), so the pet never interrupts.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("companion.pet")

# Short phrases Cozmo can say on his own, per mood and language.
PHRASES = {
    "bored": {
        "es": ["¿Alguien quiere jugar?", "Estoy aburrido", "¡Juguemos a algo!"],
        "en": ["somebody play with me", "I am bored", "let's play something"],
    },
    "happy": {
        "es": ["¡Qué día tan bueno!", "¡Estoy feliz!"],
        "en": ["what a great day", "I am happy"],
    },
    "excited": {
        "es": ["¡Wiii!", "¡Vamos, vamos!"],
        "en": ["yahoo", "let's go, let's go"],
    },
    "sad": {
        "es": ["Me siento un poco triste"],
        "en": ["I feel a little sad"],
    },
    "curious": {
        "es": ["¿Qué hay por aquí?", "Mmm, interesante"],
        "en": ["what is over here", "hmm, interesting"],
    },
}

USER_IDLE_GRACE = 15.0  # seconds of user inactivity before the pet acts


class PetMode:
    """Background autonomous behavior driven by the emotional state."""

    def __init__(
        self,
        robot,
        emotions,
        lang: str = "es",
        on_action: Optional[Callable[[str], None]] = None,
        min_interval: float = 18.0,
        max_interval: float = 45.0,
    ) -> None:
        self._robot = robot
        self._emotions = emotions
        self.lang = lang
        self._on_action = on_action
        self._min = min_interval
        self._max = max_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pet-mode", daemon=True)
        self._thread.start()
        log.info("Pet mode started")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._running = False
        log.info("Pet mode stopped")

    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.wait(random.uniform(self._min, self._max)):
            try:
                self._maybe_act()
            except Exception:  # noqa: BLE001 - the pet must never crash
                log.exception("pet mode iteration failed")

    def _maybe_act(self) -> None:
        if not self._robot.connected:
            return
        if time.time() - self._emotions.last_interaction < USER_IDLE_GRACE:
            return  # the user is actively playing; do not interrupt
        description = self._choose_and_run()
        if description and self._on_action:
            try:
                self._on_action(description)
            except Exception:  # noqa: BLE001
                pass

    def _choose_and_run(self) -> Optional[str]:
        """Pick a mood-appropriate action and queue it on the robot thread."""
        emotion = self._emotions.get()
        phrases = PHRASES.get(emotion, {}).get(self.lang) or PHRASES.get(emotion, {}).get("en", [])

        def say_phrase() -> None:
            self._robot.say(random.choice(phrases))

        options = []
        if emotion == "bored":
            options = [
                ("explora por aburrimiento", self._robot.look_around),
                ("pide atención", self._safe_anim("anim_bored_01")),
                ("baila para llamar la atención", self._robot.dance),
            ]
            if phrases:
                options.append((f"dice «{random.choice(phrases)}»", say_phrase))
        elif emotion == "curious":
            options = [
                ("explora los alrededores", self._robot.look_around),
                ("mira a un lado", lambda: self._robot.turn(random.uniform(20, 60))),
                ("mira al otro lado", lambda: self._robot.turn(-random.uniform(20, 60))),
            ]
            if phrases:
                options.append((f"dice «{random.choice(phrases)}»", say_phrase))
        elif emotion in ("happy", "excited"):
            options = [
                ("baila de alegría", self._robot.dance),
                ("hace una animación", self._safe_anim("anim_pounce_success_01")),
                ("da un paseo corto", lambda: self._robot.drive(80.0, 80.0, 1.0)),
            ]
            if phrases:
                options.append((f"dice «{random.choice(phrases)}»", say_phrase))
        elif emotion == "tired":
            options = [
                ("baja la cabeza para descansar", self._rest_pose),
                (None, None),  # often does nothing when tired
            ]
        elif emotion == "sad":
            options = [
                ("baja la cabeza", lambda: self._robot.move_head(0.1)),
                (None, None),
            ]
            if phrases:
                options.append((f"dice «{random.choice(phrases)}»", say_phrase))
        elif emotion == "scared":
            options = [
                ("retrocede con cuidado", lambda: self._robot.drive(-60.0, -60.0, 0.5)),
                (None, None),
            ]

        options = [(d, fn) for d, fn in options if fn is not None]
        if not options:
            return None

        description, action = random.choice(options)
        self._robot.submit(self._wrap(action))
        return description

    def _rest_pose(self) -> None:
        self._robot.move_head(0.0)
        time.sleep(0.3)
        self._robot.move_lift(0.0)
        self._robot.lights_off()

    def _safe_anim(self, name: str) -> Callable[[], None]:
        def run() -> None:
            self._robot.play_anim(name)
        return run

    @staticmethod
    def _wrap(fn: Callable[[], None]) -> Callable[[], None]:
        def wrapper() -> None:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log.warning("pet action failed: %s", e)
        return wrapper
