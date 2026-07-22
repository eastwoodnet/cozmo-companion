"""Offline speech recognition with Vosk + PyAudio.

Imports are lazy so the server runs fine without a microphone or model.
Usage:
    listener = VoskListener(model_path, on_partial=..., on_final=..., on_state=...)
    listener.start_listening()   # spawns a background thread
    listener.stop_listening()
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

log = logging.getLogger("companion.stt")

SAMPLE_RATE = 16000
FRAMES_PER_BUFFER = 8000
MAX_LISTEN_SECONDS = 20.0


def stt_available(model_path: Optional[Union[str, Path]]) -> bool:
    """True if vosk, pyaudio and a model directory are all present."""
    if not model_path or not Path(model_path).is_dir():
        return False
    try:
        import pyaudio  # noqa: F401
        import vosk  # noqa: F401

        return True
    except ImportError:
        return False


class VoskListener:
    """Offline recognizer running in its own thread.

    Two modes:
    - Push-to-talk (default): stops after the first complete utterance or
      after MAX_LISTEN_SECONDS.
    - Continuous (wake-word): keeps listening until stop_listening() is
      called, emitting every complete utterance through on_final.
    """

    def __init__(
        self,
        model_path: Union[str, Path],
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
        on_state: Optional[Callable[[bool], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        continuous: bool = False,
    ) -> None:
        self._model_path = str(model_path)
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_state = on_state
        self._on_error = on_error
        self._continuous = continuous
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._listening = False
        self._lock = threading.Lock()

    @property
    def listening(self) -> bool:
        with self._lock:
            return self._listening

    def start_listening(self) -> bool:
        """Start capturing audio; returns False if already listening."""
        with self._lock:
            if self._listening:
                return False
            self._listening = True
            self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vosk", daemon=True)
        self._thread.start()
        return True

    def stop_listening(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    def _set_listening(self, value: bool) -> None:
        with self._lock:
            self._listening = value
        if self._on_state:
            try:
                self._on_state(value)
            except Exception:  # noqa: BLE001
                log.exception("stt state callback failed")

    def _error(self, message: str) -> None:
        log.warning("STT error: %s", message)
        if self._on_error:
            try:
                self._on_error(message)
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        stream = None
        audio = None
        try:
            import pyaudio
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            log.info("Loading Vosk model from %s", self._model_path)
            model = Model(self._model_path)
            recognizer = KaldiRecognizer(model, SAMPLE_RATE)

            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=FRAMES_PER_BUFFER,
            )
            log.info("Listening...")
            self._set_listening(True)

            started = time.time()
            while not self._stop.is_set():
                if not self._continuous and time.time() - started > MAX_LISTEN_SECONDS:
                    log.info("Listen timeout reached")
                    break
                try:
                    data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
                except OSError:
                    break
                if recognizer.AcceptWaveform(data):
                    final_text = json.loads(recognizer.Result()).get("text", "").strip()
                    if final_text and self._on_final:
                        try:
                            self._on_final(final_text)
                        except Exception:  # noqa: BLE001
                            log.exception("stt final callback failed")
                    if final_text and not self._continuous:
                        break
                else:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
                    if partial and self._on_partial:
                        try:
                            self._on_partial(partial)
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as e:  # noqa: BLE001
            self._error(str(e) or e.__class__.__name__)
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
                if audio is not None:
                    audio.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._set_listening(False)
