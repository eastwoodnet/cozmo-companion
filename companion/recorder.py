"""Video recording from Cozmo's camera frames.

Two backends:
- OpenCV (preferred): real MP4 video.
- Pillow fallback: animated GIF (no extra dependencies).
"""
from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path
from typing import List, Optional, Union

log = logging.getLogger("companion.recorder")

try:
    import cv2
    import numpy as np

    HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    HAS_CV2 = False

from PIL import Image

MAX_FRAMES = 3000  # ~10 minutes at 5 fps — safety cap


class VideoRecorder:
    """Collects JPEG frames and encodes them on stop()."""

    def __init__(self, fps: float = 5.0) -> None:
        self._fps = max(1.0, float(fps))
        self._frames: List[bytes] = []
        self._recording = False
        self._started = 0.0
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    def start(self) -> None:
        with self._lock:
            self._frames = []
            self._started = time.time()
            self._recording = True
        log.info("Recording started")

    def add_frame(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            if self._recording and len(self._frames) < MAX_FRAMES:
                self._frames.append(jpeg_bytes)

    def stop(self, output_path: Union[str, Path]) -> Optional[Path]:
        """Encode collected frames to disk. Returns the path or None."""
        with self._lock:
            self._recording = False
            frames = list(self._frames)
            self._frames = []
        if not frames:
            log.warning("Recording stopped with no frames")
            return None

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if HAS_CV2:
            result = self._encode_mp4(frames, output_path.with_suffix(".mp4"))
            if result:
                return result
            log.warning("MP4 encoding failed, falling back to GIF")
        return self._encode_gif(frames, output_path.with_suffix(".gif"))

    # ------------------------------------------------------------------

    def _encode_mp4(self, frames: List[bytes], path: Path) -> Optional[Path]:
        try:
            first = cv2.imdecode(np.frombuffer(frames[0], dtype=np.uint8), cv2.IMREAD_COLOR)
            if first is None:
                return None
            height, width = first.shape[:2]
            writer = cv2.VideoWriter(
                str(path), cv2.VideoWriter_fourcc(*"mp4v"), self._fps, (width, height)
            )
            if not writer.isOpened():
                return None
            for jpeg in frames:
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    if frame.shape[:2] != (height, width):
                        frame = cv2.resize(frame, (width, height))
                    writer.write(frame)
            writer.release()
            log.info("Saved video %s (%d frames)", path, len(frames))
            return path
        except Exception as e:  # noqa: BLE001
            log.warning("MP4 encode error: %s", e)
            return None

    def _encode_gif(self, frames: List[bytes], path: Path) -> Optional[Path]:
        try:
            images = [Image.open(io.BytesIO(j)).convert("P", palette=Image.ADAPTIVE) for j in frames]
            duration_ms = int(1000 / self._fps)
            images[0].save(
                path,
                save_all=True,
                append_images=images[1:],
                duration=duration_ms,
                loop=0,
                optimize=True,
            )
            log.info("Saved GIF %s (%d frames)", path, len(frames))
            return path
        except Exception as e:  # noqa: BLE001
            log.warning("GIF encode error: %s", e)
            return None
