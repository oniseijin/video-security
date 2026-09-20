from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LEVEL_ACCURATE = 0
LEVEL_FAST = 1
LEVEL_RAPID = 2


@dataclass
class OCRResult:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]


def _image_to_nsdata(image: np.ndarray) -> object:
    import cv2

    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encode failed")
    raw = buf.tobytes()
    from Foundation import NSData

    return NSData.dataWithBytes_length_(raw, len(raw))


def vision_ocr(image: np.ndarray, level: int = LEVEL_FAST) -> list[OCRResult]:
    try:
        from Vision import VNImageRequestHandler, VNRecognizeTextRequest
    except Exception:
        return []
    if image.ndim != 3 or image.shape[2] < 3:
        return []
    try:
        nsdata = _image_to_nsdata(image)
        handler = VNImageRequestHandler.alloc().initWithData_options_(nsdata, None)
        req = VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(level)
        ok, _err = handler.performRequests_error_([req], None)
        if not ok or req.results() is None:
            return []
        out: list[OCRResult] = []
        for obs in req.results():
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            t = cands[0]
            text = str(t.string())
            try:
                conf = float(t.confidence())
            except (TypeError, ValueError):
                conf = 0.0
            if conf != conf:
                conf = 0.0
            bb = obs.boundingBox()
            out.append(
                OCRResult(
                    text=text,
                    confidence=conf,
                    bbox=(
                        float(bb.origin.x),
                        float(bb.origin.y),
                        float(bb.size.width),
                        float(bb.size.height),
                    ),
                )
            )
        return out
    except Exception:
        return []


def upscale_crop(image: np.ndarray, min_width: int = 200) -> np.ndarray:
    import cv2

    h, w = image.shape[:2]
    factor = 2 if w >= min_width else 4
    return cv2.resize(image, (w * factor, h * factor), interpolation=cv2.INTER_CUBIC)
