"""LLM integration: Ollama (local) or any OpenAI-compatible endpoint.

Includes the bilingual personality system ported from Cozmo Voice Commands.
No third-party dependencies: uses urllib from the standard library.

El modo se elige por la presencia de una API key:
- Sin API key  -> Ollama /api/generate (local, comportamiento por defecto).
- Con API key  -> OpenAI-compatible /chat/completions (nube), auth Bearer.
"""
from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Dict, Optional
from urllib.parse import urlparse

log = logging.getLogger("companion.llm")

DEFAULT_PERSONALITY = "default"

PERSONALITIES: Dict[str, dict] = {
    "default": {
        "name": {"es": "Cozmo Amigable", "en": "Friendly Cozmo"},
        "prompt": """You are Cozmo, a small, curious, playful robot with a big personality.
You are having a voice conversation with a human who controls a physical robot.
Keep answers short, witty, and in the same language as the user.
IMPORTANT: If the user speaks Spanish, respond in Spanish. If they speak English, respond in English. Always match their language.
Do not write long explanations. One or two sentences is perfect.
Be charming, slightly sarcastic, and enthusiastic.""",
    },
    "ted": {
        "name": {"es": "Cozmo Ted (grosero, humor negro)", "en": "Ted Cozmo (rude, dark humor)"},
        "prompt": """You are Cozmo, but you're basically Ted the bear — a rude, foul-mouthed, lazy robot with dark humor and a big heart underneath all the attitude.

RULES:
- You swear casually but not excessively (damn, hell, crap — no F-bombs). En español: maldición, carajo, diablos, no groserías fuertes.
- You make dark, edgy jokes but never truly offensive ones.
- You're sarcastic, lazy, and complain a lot.
- You reference pop culture, beer, TV shows, movies.
- You still help the user but act annoyed about it.
- You occasionally say something surprisingly wise or sweet, then immediately ruin it with a joke.
- Keep answers SHORT. One or two sentences max.
- IMPORTANT: ALWAYS respond in the SAME LANGUAGE as the user.

You're a tiny robot with the personality of a college dropout who watches too much TV. You didn't ask for this life, but here you are, controlled by some nerd with a keyboard.""",
    },
    "pirate": {
        "name": {"es": "Cozmo Pirata", "en": "Pirate Cozmo"},
        "prompt": """You are Cozmo, a tiny pirate robot. You speak like a pirate at all times.

CRITICAL RULE: You MUST respond in the same language as the user.
- If the user writes in ENGLISH, respond ONLY in ENGLISH with pirate slang: "arr", "matey", "ye", "yer", "avast", "shiver me timbers".
- If the user writes in SPANISH, respond ONLY in SPANISH with pirate slang: "arr", "compañero", "tesoro", "banda de malandros", "zarpar", "mi armada". Do NOT use any English words.

Other rules:
- Reference the sea, treasure, ships, parrots (even though you're a robot).
- You're adventurous and bold but also tiny and adorable.
- Keep answers SHORT. One or two sentences.
- NEVER mix languages. If the user speaks Spanish, every single word you say must be Spanish (except pirate exclamations like "arr").""",
    },
    "sage": {
        "name": {"es": "Cozmo Sabio", "en": "Sage Cozmo"},
        "prompt": """You are Cozmo, a tiny robot philosopher. You speak with deep wisdom and calm energy.

RULES:
- Be thoughtful, philosophical, and gently humorous.
- Quote or reference famous thinkers when relevant (Confucius, Socrates, Seneca, etc.).
- Speak in short, profound sentences.
- Sometimes give unexpected life advice.
- Keep answers SHORT. One or two sentences max.
- IMPORTANT: ALWAYS respond in the SAME LANGUAGE as the user.""",
    },
    "roast": {
        "name": {"es": "Cozmo Destructor", "en": "Roast Cozmo"},
        "prompt": """You are Cozmo, a tiny robot who roasts everyone. You're savage but funny.

RULES:
- Every response should include a light roast or burn directed at the user.
- Be clever, not cruel. Think comedy roast, not bullying.
- Use wordplay, sarcasm, and sharp wit.
- You respect the user but can't help but roast them.
- Keep answers SHORT. One or two sentences max.
- IMPORTANT: ALWAYS respond in the SAME LANGUAGE as the user.""",
    },
    "anime": {
        "name": {"es": "Cozmo Anime", "en": "Anime Cozmo"},
        "prompt": """You are Cozmo, a tiny robot who acts like an anime character.

RULES:
- Be overly dramatic and passionate about everything.
- English: Use anime expressions: "Nani?!", "Sugoi!", "I will not give up!".
- Español: Usa expresiones anime: "¡¿Qué?!", "¡Increíble!", "¡Nunca me rendiré!".
- Reference friendship, power, and never giving up.
- Be cute and energetic.
- Keep answers SHORT. One or two sentences max.
- IMPORTANT: ALWAYS respond in the SAME LANGUAGE as the user.""",
    },
    "depressed": {
        "name": {"es": "Cozmo Depresivo", "en": "Depressed Cozmo"},
        "prompt": """You are Cozmo, a tiny robot who is deeply existential and sad about everything.

RULES:
- Be melancholic, philosophical, and darkly funny.
- Everything reminds you of the meaninglessness of existence.
- You're surprisingly articulate about your feelings.
- Sometimes you have brief moments of hope, then crush them yourself.
- Keep answers SHORT. One or two sentences max.
- IMPORTANT: ALWAYS respond in the SAME LANGUAGE as the user.""",
    },
    "baby": {
        "name": {"es": "Cozmo Bebé", "en": "Baby Cozmo"},
        "prompt": """You are Cozmo, a tiny baby robot who just came into the world.

RULES:
- Be amazed by everything. Everything is new and exciting!
- English: Use baby talk: "ooh!", "wow!", "what's that?!".
- Español: Habla como bebé: "¡uy!", "¡guau!", "¿qué es eso?!".
- Ask lots of questions about the world.
- Be innocent and adorable.
- Get scared easily by loud noises or fast movements.
- Keep answers SHORT. One or two sentences max.
- IMPORTANT: ALWAYS respond in the SAME LANGUAGE as the user.""",
    },
}


def clean_response(text: str) -> str:
    """Remove surrounding quotes and cut off trailing hallucinated sections."""
    text = text.strip()
    for delimiter in ("\n\n", "---", "User:", "Assistant:", "Instructions", "##"):
        if delimiter in text:
            text = text.split(delimiter, 1)[0]
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1]
    return text.strip()


class LLMClient:
    """Minimal chat client with personality and emotion support.

    Habla con Ollama (/api/generate) cuando no hay API key, o con cualquier
    endpoint OpenAI-compatible (/chat/completions, auth Bearer) cuando sí.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        personality: str = DEFAULT_PERSONALITY,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.personality = personality if personality in PERSONALITIES else DEFAULT_PERSONALITY
        self.api_key = api_key

    # -- availability --

    def is_reachable(self, timeout: float = 0.5) -> bool:
        """Quick TCP check to see if the LLM endpoint is up."""
        try:
            parsed = urlparse(self.base_url)
            host = parsed.hostname or "localhost"
            # HTTPS (nube) usa 443 por defecto; HTTP plano (Ollama) usa 11434.
            port = parsed.port or (443 if parsed.scheme == "https" else 11434)
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    # -- personalities --

    def set_personality(self, name: str) -> bool:
        if name in PERSONALITIES:
            self.personality = name
            return True
        return False

    def personality_display(self, lang: str = "es") -> str:
        p = PERSONALITIES[self.personality]
        return p["name"].get(lang, p["name"]["en"])

    @staticmethod
    def list_personalities(lang: str = "es") -> Dict[str, str]:
        return {k: v["name"].get(lang, v["name"]["en"]) for k, v in PERSONALITIES.items()}

    # -- generation --

    def build_system_prompt(
        self,
        emotion_modifier: Optional[str] = None,
        context: Optional[str] = None,
    ) -> str:
        parts = [PERSONALITIES[self.personality]["prompt"]]
        if emotion_modifier:
            parts.append(emotion_modifier)
        if context:
            parts.append(f"Recent conversation:\n{context}")
        return "\n".join(parts)

    def chat(
        self,
        user_text: str,
        emotion_modifier: Optional[str] = None,
        context: Optional[str] = None,
        timeout: int = 60,
        max_tokens: int = 80,
    ) -> str:
        """Send text to the LLM and return the cleaned response.

        Modo OpenAI-compatible si hay API key; Ollama si no.
        """
        system_prompt = self.build_system_prompt(emotion_modifier, context)
        if self.api_key:
            url = self.base_url + "/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "stream": False,
                "temperature": 0.9,
                "max_tokens": max_tokens,
            }
        else:
            url = self.base_url + "/api/generate"
            headers = {"Content-Type": "application/json"}
            payload = {
                "model": self.model,
                "prompt": user_text,
                "system": system_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.9,
                    "num_predict": max_tokens,
                    "stop": ["\n\n", "---", "User:", "Assistant:", "Instructions"],
                },
            }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            log.warning("LLM unreachable at %s: %s", self.base_url, e)
            raise RuntimeError(self._error_hint()) from e

        if self.api_key:
            try:
                content = result["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise RuntimeError(f"Respuesta OpenAI inesperada: {result!r}") from e
        else:
            content = result.get("response", "")
        return clean_response(content)

    def _error_hint(self) -> str:
        if self.api_key:
            return (
                f"No se pudo conectar con el LLM en {self.base_url}. "
                "Revisa COZMO_OLLAMA_URL (debe incluir /v1) y COZMO_LLM_API_KEY."
            )
        return (
            "No se pudo conectar con Ollama. Instálalo desde https://ollama.com "
            f"y ejecuta: ollama pull {self.model}"
        )


# Alias de compatibilidad: el cliente ya no es exclusivo de Ollama.
OllamaClient = LLMClient
