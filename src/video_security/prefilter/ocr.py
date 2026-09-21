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


def vision_ocr(
    image: np.ndarray,
    level: int = LEVEL_FAST,
    languages: list[str] | None = None,
) -> list[OCRResult]:
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
        if languages is not None:
            req.setRecognitionLanguages_(languages)
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


def sharpness_luma(image: np.ndarray) -> tuple[float, float]:
    import cv2

    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    luma = float(gray.mean())
    return sharp, luma


def median_stack(images: list[np.ndarray]) -> np.ndarray:
    import cv2

    if not images:
        raise ValueError("median_stack needs images")
    if len(images) == 1:
        return images[0]
    heights = [i.shape[0] for i in images]
    widths = [i.shape[1] for i in images]
    target_h = sorted(heights)[len(heights) // 2]
    target_w = sorted(widths)[len(widths) // 2]
    resized = [
        (
            i
            if i.shape[0] == target_h and i.shape[1] == target_w
            else cv2.resize(i, (target_w, target_h), interpolation=cv2.INTER_AREA)
        )
        for i in images
    ]
    stacked = np.median(np.stack(resized), axis=0).astype(np.uint8)
    return np.asarray(stacked)
