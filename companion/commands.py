"""Trilingual (ES/EN/中文) natural language command parser.

Maps spoken or typed phrases to robot actions. Returns a Command or None
when the text is not a command (in which case it can be sent to the LLM).

Chinese has no word separators, so Chinese keywords, colors and numbers are
matched by substring instead of by token.

Examples:
    "cozmo avanza 2"      -> Command("forward", seconds=2)
    "cozmo, dance"        -> Command("dance")
    "di hola mundo"       -> Command("say", text="hola mundo")
    "luces rojas"         -> Command("lights", color="red")
    "前进二"              -> Command("forward", number=2)
    "红灯"                -> Command("lights", color="red")
    "说 你好"             -> Command("say", text="你好")
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

# 中文数字：单字、无空格，需按子串匹配（"前进二" -> 2）。
CN_NUMBERS: Dict[str, int] = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 中文颜色：单字 -> 规范色名（供灯光子串匹配）。
CN_COLORS: Dict[str, str] = {
    "红": "red", "绿": "green", "蓝": "blue", "白": "white",
    "黄": "yellow", "紫": "purple", "橙": "orange",
}

# 中文灯光关键词（出现即视为灯光命令）。
CN_LIGHT_WORDS = ("灯", "灯光")

# Command keywords: action -> trigger words (all lowercase, ES + EN + 中文).
# 中文触发词按子串匹配（无空格），均取 >=2 字以减少误触。
KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "forward": ("adelante", "avanza", "avanzar", "recto", "forward", "ahead", "straight",
                "前进", "向前", "往前走", "往前", "直行"),
    "backward": ("atrás", "atras", "retrocede", "retroceder", "backward", "back", "reverse",
                 "后退", "向后", "倒退", "退后", "往回走"),
    "left": ("izquierda", "left", "左转", "向左转", "向左"),
    "right": ("derecha", "right", "右转", "向右转", "向右"),
    "dance": ("baila", "bailar", "danza", "dance", "jump", "salta",
              "跳舞", "跳个舞", "来点舞", "来段舞"),
    "look": ("mira", "explora", "busca", "look", "explore", "search",
             "看看周围", "四处看看", "环顾", "看看"),
    "photo": ("foto", "photo", "picture", "captura",
              "拍照", "拍张照", "拍个照", "照片"),
    "sleep": ("duerme", "dormir", "descansa", "sleep", "night", "buenas",
              "睡觉", "去睡", "睡吧", "休息"),
    "happy": ("feliz", "alegre", "happy", "cheer", "开心", "高兴", "快乐"),
    "sad": ("triste", "sad", "难过", "伤心"),
    "mood": ("cómo estás", "como estas", "qué tal", "que tal", "how are you", "mood", "feeling",
             "你怎么样", "你好吗", "感觉怎么样", "心情怎么样", "最近怎么样"),
    "lift_up": ("sube brazo", "brazo arriba", "levanta", "lift up", "raise lift",
                "举起手臂", "抬起手臂", "手臂起来", "举手臂"),
    "lift_down": ("baja brazo", "brazo abajo", "lift down", "lower lift",
                  "放下手臂", "手臂下去", "放低手臂", "放下手臂"),
    "head_up": ("cabeza arriba", "levanta cabeza", "head up", "look up",
                "抬头", "抬起头", "头抬起"),
    "head_down": ("cabeza abajo", "baja cabeza", "head down", "look down",
                  "低头", "低下头", "头低下"),
}

ACTIVATION_WORDS = ("cozmo", "cosmo", "robot", "ok", "机器人")


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
    # 同时清理西/英标点与中文标点（？！。，、；：及引号括号）。
    text = re.sub(r"[¿?¡!.,;:？！。，、；：“”'‘’（）()【】\[\]]", " ", text)
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
    # 中文数字是单字且无空格分隔，按子串匹配（"前进二" -> 2）。
    for word, value in CN_NUMBERS.items():
        if word in text:
            return float(value)
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
    # 中文触发词 "说"/"念" 为单字、无尾随空格（中文不分词）。
    for trigger in ("di ", "dec ", "say ", "repite ", "说", "念"):
        if text.startswith(trigger):
            payload = text[len(trigger):].strip()
            if payload:
                return Command("say", text=payload, raw=raw)

    # Lights: needs a light keyword + a color word.
    if tokens & {"luz", "luces", "light", "lights", "lámpara", "lampara"}:
        for word in tokens:
            if word in COLOR_WORDS:
                return Command("lights", color=COLOR_WORDS[word], raw=raw)

    # Lights (中文): 无空格分词，按子串匹配；"关" 表示关灯。
    if any(w in text for w in CN_LIGHT_WORDS):
        if "关" in text:
            return Command("lights", color="off", raw=raw)
        for char, canonical in CN_COLORS.items():
            if char in text:
                return Command("lights", color=canonical, raw=raw)

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
