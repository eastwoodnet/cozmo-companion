"""Camera-based perception: face detection with OpenCV Haar cascades.

OpenCV is imported lazily — the app runs normally without it, just with the
face-detection toggle hidden. The cascade ships inside the opencv package,
so no model download is required.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

log = logging.getLogger("companion.perception")

try:
    import cv2
    import numpy as np

    HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    HAS_CV2 = False

# (x, y, w, h) in pixels of the analyzed frame.
FaceBox = Tuple[int, int, int, int]


def faces_available() -> bool:
    """True if OpenCV and a Haar cascade are usable."""
    if not HAS_CV2:
        return False
    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        return not cascade.empty()
    except Exception:  # noqa: BLE001
        return False


class FaceDetector:
    """Detects faces in JPEG frames from Cozmo's camera."""

    def __init__(self, scale_factor: float = 1.2, min_neighbors: int = 5) -> None:
        if not HAS_CV2:
            raise RuntimeError("opencv no está instalado (pip install opencv-python-headless)")
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)
        if self._cascade.empty():
            raise RuntimeError("No se pudo cargar el clasificador Haar")
        self._scale = scale_factor
        self._neighbors = min_neighbors

    def detect_jpeg(self, jpeg_bytes: bytes) -> Tuple[List[FaceBox], Tuple[int, int]]:
        """Detect faces in a JPEG image.

        Returns (faces, (frame_width, frame_height)). Faces are sorted by
        area, largest first. Coordinates refer to the decoded frame.
        """
        frame = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return [], (0, 0)
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self._cascade.detectMultiScale(
            gray,
            scaleFactor=self._scale,
            minNeighbors=self._neighbors,
            minSize=(30, 30),
        )
        boxes = sorted(((int(x), int(y), int(w), int(h)) for x, y, w, h in faces),
                       key=lambda b: b[2] * b[3], reverse=True)
        return boxes, (width, height)


def turn_angle_for_face(face: FaceBox, frame_width: int, max_angle: float = 35.0) -> Optional[float]:
    """Compute a small turn to center the largest face.

    Positive = turn left (face is left of center), negative = right.
    Returns None when the face is already roughly centered.
    """
    if frame_width <= 0:
        return None
    x, _, w, _ = face
    offset = (x + w / 2 - frame_width / 2) / (frame_width / 2)  # -1..1
    if abs(offset) < 0.25:
        return None
    # Robot turns left with positive angles; a face on the left (offset<0)
    # needs a positive (left) turn.
    return max(-max_angle, min(max_angle, -offset * max_angle))
