"""On-device OCR for embedded images using Apple's Vision framework.

Used only for images embedded in the DOCX and only when OCR is enabled in
settings. Ordinary Word text is never OCR'd — it is read from the XML.
Runs entirely on the Mac (no network).
"""

from __future__ import annotations

from typing import Iterator

OCR_FORMATS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".webp")


def available() -> bool:
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def recognize(data: bytes) -> Iterator[dict]:
    """Yield recognised words with normalised bounding boxes (origin top-left)."""
    import Quartz
    import Vision
    from Foundation import NSData

    ns = NSData.dataWithBytes_length_(data, len(data))
    src = Quartz.CGImageSourceCreateWithData(ns, None)
    if src is None:
        return
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if img is None:
        return
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, _err = handler.performRequests_error_([req], None)
    if not ok:
        return
    line_no = 0
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        c = cand[0]
        text = str(c.string())
        conf = float(c.confidence())
        # Per-word boxes via character ranges.
        pos = 0
        for word in text.split():
            idx = text.find(word, pos)
            pos = idx + len(word)
            box = None
            try:
                from Foundation import NSMakeRange

                rect_obs, _ = c.boundingBoxForRange_error_(NSMakeRange(idx, len(word)), None)
                if rect_obs is not None:
                    bb = rect_obs.boundingBox()
                    box = [bb.origin.x, 1 - bb.origin.y - bb.size.height, bb.size.width, bb.size.height]
            except Exception:  # noqa: BLE001
                bb = obs.boundingBox()
                box = [bb.origin.x, 1 - bb.origin.y - bb.size.height, bb.size.width, bb.size.height]
            yield {"text": word, "confidence": round(conf, 3), "box": box, "line": line_no}
        line_no += 1
