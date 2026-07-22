"""Emotional state system for Cozmo Companion.

Tracks Cozmo's mood and translates it into backpack lights, animations and
LLM prompt modifiers. Bilingual (ES/EN). The state object is UI-agnostic:
it notifies listeners through an on_change callback.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

# Emotion -> backpack light RGB.
EMOTION_COLORS: Dict[str, tuple] = {
    "happy": (0, 255, 0),
    "sad": (0, 0, 255),
    "curious": (255, 255, 255),
    "excited": (255, 80, 0),
    "tired": (0, 0, 128),
    "bored": (255, 255, 0),
    "scared": (255, 0, 0),
}

EMOTIONS: Dict[str, dict] = {
    "happy": {
        "animation": "anim_pounce_success_01",
        "modifier": {
            "es": "Te sientes feliz y juguetón. Sé alegre y enérgico.",
            "en": "You are feeling happy and playful. Be cheerful and energetic.",
        },
        "boredom_rate": -2,
        "emoji": "😊",
    },
    "sad": {
        "animation": "anim_memorymatch_failhand_01",
        "modifier": {
            "es": "Te sientes un poco triste. Sé melodramático pero adorable.",
            "en": "You are feeling a little sad. Be melodramatic but still sweet.",
        },
        "boredom_rate": 1,
        "emoji": "😢",
    },
    "curious": {
        "animation": "anim_knowledgegraph_getin_01",
        "modifier": {
            "es": "Te sientes curioso. Haz preguntas y explora ideas.",
            "en": "You are feeling curious. Ask questions and explore ideas.",
        },
        "boredom_rate": -1,
        "emoji": "🤔",
    },
    "excited": {
        "animation": "anim_speedtap_wingame_intensity02_01",
        "modifier": {
            "es": "¡Estás muy emocionado! Sé entusiasta y eufórico.",
            "en": "You are very excited! Be enthusiastic and upbeat.",
        },
        "boredom_rate": -3,
        "emoji": "🤩",
    },
    "tired": {
        "animation": "anim_gotosleep_sleeping_01",
        "modifier": {
            "es": "Tienes sueño. Habla lento, bosteza y pide descanso.",
            "en": "You are tired and sleepy. Be slow, yawny, and want to rest.",
        },
        "boredom_rate": 0,
        "emoji": "😴",
    },
    "bored": {
        "animation": "anim_bored_01",
        "modifier": {
            "es": "Estás aburrido. Quejate un poco y pide atención o un juego.",
            "en": "You are bored. Be a bit whiny and ask for attention or a game.",
        },
        "boredom_rate": 2,
        "emoji": "🙄",
    },
    "scared": {
        "animation": "anim_pounce_reacttoobj_01_shorter",
        "modifier": {
            "es": "Estás asustado y cauteloso. Sé nervioso pero valiente.",
            "en": "You are startled and cautious. Be jumpy but brave.",
        },
        "boredom_rate": -1,
        "emoji": "😨",
    },
}

# Word triggers for automatic emotion detection (bilingual).
POSITIVE_WORDS = {
    "hello", "hi", "friend", "love", "good", "great", "awesome", "nice", "thanks",
    "hola", "amigo", "amiga", "gracias", "genial", "bueno", "increíble", "quiero",
}
NEGATIVE_WORDS = {
    "bad", "stupid", "dumb", "hate", "ugly", "stop", "shut",
    "malo", "tonto", "feo", "odio", "para", "basta", "cállate", "callate",
}
EXCITED_WORDS = {
    "wow", "amazing", "cool", "party", "dance", "fun", "yay",
    "baila", "bailar", "fiesta", "guau", "guay", "vamos", "jugar",
}
SCARY_WORDS = {"boo", "scary", "monster", "ghost", "bú", "buu", "monstruo", "fantasma", "miedo"}


class EmotionState:
    """Thread-safe mood tracker with change notification."""

    def __init__(
        self,
        initial: str = "curious",
        on_change: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """on_change(new_emotion, old_emotion) is called outside the lock."""
        self._emotion = initial if initial in EMOTIONS else "curious"
        self._lock = threading.Lock()
        self._last_interaction = time.time()
        self._boredom = 0.0
        self._on_change = on_change

    def get(self) -> str:
        with self._lock:
            return self._emotion

    def info(self) -> dict:
        with self._lock:
            data = EMOTIONS[self._emotion]
            return {
                "name": self._emotion,
                "emoji": data["emoji"],
                "color": EMOTION_COLORS[self._emotion],
                "boredom": round(self._boredom, 1),
            }

    def set(self, emotion: str, reason: Optional[str] = None) -> bool:
        emotion = emotion.lower().strip()
        if emotion not in EMOTIONS:
            return False
        with self._lock:
            old = self._emotion
            self._emotion = emotion
            self._last_interaction = time.time()
            if reason:
                self._boredom = max(0.0, self._boredom - 5)
        if emotion != old and self._on_change:
            self._on_change(emotion, old)
        return True

    def modifier(self, lang: str = "es") -> str:
        with self._lock:
            mods = EMOTIONS[self._emotion]["modifier"]
            return mods.get(lang, mods["en"])

    def animation(self) -> str:
        with self._lock:
            return EMOTIONS[self._emotion]["animation"]

    def color(self) -> tuple:
        with self._lock:
            return EMOTION_COLORS[self._emotion]

    def interact(self) -> None:
        """Reset boredom when the user interacts."""
        with self._lock:
            self._last_interaction = time.time()
            self._boredom = max(0.0, self._boredom - 3)

    def tick(self, dt: float = 1.0) -> bool:
        """Advance boredom; may transition to bored/tired. Returns True if mood changed."""
        with self._lock:
            rate = EMOTIONS[self._emotion]["boredom_rate"]
            self._boredom = max(0.0, min(30.0, self._boredom + rate * dt))
            elapsed = time.time() - self._last_interaction
            new_emotion = None
            if self._boredom > 20 and self._emotion != "bored":
                new_emotion = "bored"
            elif self._emotion == "bored" and elapsed > 90:
                new_emotion = "tired"
        if new_emotion:
            return self.set(new_emotion, reason="idle")
        return False

    def detect_from_text(self, text: str) -> bool:
        """Guess an emotion from user text and apply it. Returns True if applied."""
        tokens = set(text.lower().split())
        if tokens & SCARY_WORDS:
            return self.set("scared", reason="startled")
        if tokens & EXCITED_WORDS:
            return self.set("excited", reason="user excited")
        if tokens & NEGATIVE_WORDS:
            return self.set("sad", reason="user negative")
        if tokens & POSITIVE_WORDS:
            return self.set("happy", reason="user positive")
        return False


def emotion_list() -> List[str]:
    return list(EMOTIONS.keys())
