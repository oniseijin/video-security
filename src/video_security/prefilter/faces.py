from __future__ import annotations

import numpy as np

FaceBox = tuple[float, float, float, float]


def _convert_box(origin_x: float, origin_y: float, w: float, h: float) -> FaceBox:
    return (origin_x, 1.0 - (origin_y + h), w, h)


def _face_request(nsdata: object) -> list[FaceBox]:
    from Vision import VNDetectFaceRectanglesRequest, VNImageRequestHandler

    handler = VNImageRequestHandler.alloc().initWithData_options_(nsdata, None)
    req = VNDetectFaceRectanglesRequest.alloc().init()
    ok, _err = handler.performRequests_error_([req], None)
    if not ok or req.results() is None:
        return []
    boxes: list[FaceBox] = []
    for obs in req.results():
        bb = obs.boundingBox()
        boxes.append(
            _convert_box(
                float(bb.origin.x),
                float(bb.origin.y),
                float(bb.size.width),
                float(bb.size.height),
            )
        )
    return boxes


def _image_to_nsdata(image: np.ndarray) -> object:
    import cv2

    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encode failed")
    raw = buf.tobytes()
    from Foundation import NSData

    return NSData.dataWithBytes_length_(raw, len(raw))


def detect_faces(image: np.ndarray) -> list[FaceBox]:
    if image.ndim != 3 or image.shape[2] < 3:
        return []
    try:
        nsdata = _image_to_nsdata(image)
        return _face_request(nsdata)
    except Exception:
        return []