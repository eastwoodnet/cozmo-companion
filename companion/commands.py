"""Bilingual (ES/EN) natural language command parser.

Maps spoken or typed phrases to robot actions. Returns a Command or None
when the text is not a command (in which case it can be sent to the LLM).

Examples:
    "cozmo avanza 2"      -> Command("forward", seconds=2)
    "cozmo, dance"        -> Command("dance")
    "di hola mundo"       -> Command("say", text="hola mundo")
    "luces rojas"         -> Command("lights", color="red")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# Canonical color names -> RGB.
COLORS: Dict[str, Tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
    "purple": (128, 0, 128),
    "orange": (255, 165, 0),
    "off": (0, 0, 0),
}

COLOR_WORDS: Dict[str, str] = {
    # ES
    "rojo": "red", "roja": "red", "rojos": "red", "rojas": "red",
    "verde": "green", "verdes": "green",
    "azul": "blue", "azules": "blue",
    "blanco": "white", "blanca": "white",
    "amarillo": "yellow", "amarilla": "yellow",
    "morado": "purple", "morada": "purple", "violeta": "purple",
    "naranja": "orange", "anaranjado": "orange",
    "apagadas": "off", "apagado": "off", "apaga": "off",
    # EN
    "red": "red", "green": "green", "blue": "blue", "white": "white",
    "yellow": "yellow", "purple": "purple", "orange": "orange", "off": "off",
}

WORD_NUMBERS: Dict[str, int] = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Command keywords: action -> trigger words (all lowercase, ES + EN).
KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "forward": ("adelante", "avanza", "avanzar", "recto", "forward", "ahead", "straight"),
    "backward": ("atrás", "atras", "retrocede", "retroceder", "backward", "back", "reverse"),
    "left": ("izquierda", "left"),
    "right": ("derecha", "right"),
    "dance": ("baila", "bailar", "danza", "dance", "jump", "salta"),
    "look": ("mira", "explora", "busca", "look", "explore", "search"),
    "photo": ("foto", "photo", "picture", "captura"),
    "sleep": ("duerme", "dormir", "descansa", "sleep", "night", "buenas"),
    "happy": ("feliz", "alegre", "happy", "cheer"),
    "sad": ("triste", "sad"),
    "mood": ("cómo estás", "como estas", "qué tal", "que tal", "how are you", "mood", "feeling"),
    "lift_up": ("sube brazo", "brazo arriba", "levanta", "lift up", "raise lift"),
    "lift_down": ("baja brazo", "brazo abajo", "lift down", "lower lift"),
    "head_up": ("cabeza arriba", "levanta cabeza", "head up", "look up"),
    "head_down": ("cabeza abajo", "baja cabeza", "head down", "look down"),
}

ACTIVATION_WORDS = ("cozmo", "cosmo", "robot", "ok")


@dataclass
class Command:
    """A parsed robot command."""

    action: str
    number: Optional[float] = None
    text: str = ""
    color: Optional[str] = None
    raw: str = field(default="")

    def describe(self, lang: str = "es") -> str:
        """Human-readable summary of the parsed command."""
        descriptions = {
            "forward": {"es": f"adelante {self.number or 1}s", "en": f"forward {self.number or 1}s"},
            "backward": {"es": f"atrás {self.number or 1}s", "en": f"backward {self.number or 1}s"},
            "left": {"es": f"girar izquierda {self.number or 90}°", "en": f"turn left {self.number or 90}°"},
            "right": {"es": f"girar derecha {self.number or 90}°", "en": f"turn right {self.number or 90}°"},
            "dance": {"es": "bailar", "en": "dance"},
            "look": {"es": "mirar alrededor", "en": "look around"},
            "photo": {"es": "tomar foto", "en": "take photo"},
            "sleep": {"es": "ir a dormir", "en": "go to sleep"},
            "happy": {"es": "ponerse feliz", "en": "be happy"},
            "sad": {"es": "ponerse triste", "en": "be sad"},
            "mood": {"es": "decir su estado", "en": "say mood"},
            "lift_up": {"es": "subir brazo", "en": "lift up"},
            "lift_down": {"es": "bajar brazo", "en": "lift down"},
            "head_up": {"es": "cabeza arriba", "en": "head up"},
            "head_down": {"es": "cabeza abajo", "en": "head down"},
            "say": {"es": f"decir «{self.text}»", "en": f"say «{self.text}»"},
            "lights": {"es": f"luces {self.color}", "en": f"lights {self.color}"},
        }
        entry = descriptions.get(self.action, {"es": self.action, "en": self.action})
        return entry.get(lang, entry["en"])


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[¿?¡!.,;:]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_activation(text: str) -> str:
    """Remove leading activation words like 'cozmo,' or 'hey robot'."""
    words = text.split()
    while words and words[0] in ACTIVATION_WORDS:
        words.pop(0)
    return " ".join(words)


def _extract_number(text: str) -> Optional[float]:
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match:
        return float(match.group())
    for word in text.split():
        if word in WORD_NUMBERS:
            return float(WORD_NUMBERS[word])
    return None


def parse(text: str) -> Optional[Command]:
    """Parse text into a Command, or None if it is not a robot command."""
    if not text:
        return None
    raw = text
    text = _strip_activation(_normalize(text))
    if not text:
        return None

    number = _extract_number(text)
    tokens = set(text.split())

    # "say X" / "di X" — keep the remainder verbatim.
    for trigger in ("di ", "dec ", "say ", "repite "):
        if text.startswith(trigger):
            payload = text[len(trigger):].strip()
            if payload:
                return Command("say", text=payload, raw=raw)

    # Lights: needs a light keyword + a color word.
    if tokens & {"luz", "luces", "light", "lights", "lámpara", "lampara"}:
        for word in tokens:
            if word in COLOR_WORDS:
                return Command("lights", color=COLOR_WORDS[word], raw=raw)

    # Keyword commands (longest trigger match wins so "cabeza arriba" beats "arriba").
    best: Optional[Tuple[str, str]] = None  # (action, trigger)
    for action, triggers in KEYWORDS.items():
        for trigger in triggers:
            if trigger in text:
                if best is None or len(trigger) > len(best[1]):
                    best = (action, trigger)
    if best:
        return Command(best[0], number=number, raw=raw)

    return None
