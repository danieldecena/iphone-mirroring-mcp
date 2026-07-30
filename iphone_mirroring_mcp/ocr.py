"""On-device OCR of the captured window using Apple's Vision framework.

macOS ships a high-quality text recognizer (the same one behind Live Text). We
call it through pyobjc so there is no external binary to install (no tesseract).
Each recognized line comes back with the text plus the pixel center and the
screen tap point, so an agent can locate a control by its label and tap it
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import Quartz
import Vision

from . import window


@dataclass
class TextItem:
    text: str
    confidence: float
    # Center of the text box, in screenshot pixels.
    px: int
    py: int
    # Same point mapped to a global screen tap coordinate.
    screen_x: int
    screen_y: int


def _cgimage_from_png(path: str):
    url = Quartz.CFURLCreateWithFileSystemPath(
        None, path, Quartz.kCFURLPOSIXPathStyle, False
    )
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        raise RuntimeError("Could not read captured image for OCR.")
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def recognize(png_path: str, state: "window.CaptureState") -> List[TextItem]:
    """Run text recognition on a captured PNG file, returning located lines."""
    cg_image = _cgimage_from_png(png_path)
    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cg_image, None
    )
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")

    items: List[TextItem] = []
    for obs in request.results() or []:
        candidate = obs.topCandidates_(1)
        if not candidate:
            continue
        text = candidate[0].string()
        confidence = float(candidate[0].confidence())
        # Vision boundingBox is normalized with a BOTTOM-left origin.
        bbox = obs.boundingBox()
        cx_norm = bbox.origin.x + bbox.size.width / 2.0
        cy_norm = bbox.origin.y + bbox.size.height / 2.0
        px = int(cx_norm * width)
        py = int((1.0 - cy_norm) * height)  # flip to top-left origin (pixels)
        sx, sy = state.image_px_to_screen_pt(px, py)
        items.append(
            TextItem(
                text=text,
                confidence=round(confidence, 3),
                px=px,
                py=py,
                screen_x=round(sx),
                screen_y=round(sy),
            )
        )
    # Reading order: top-to-bottom, then left-to-right.
    items.sort(key=lambda it: (it.py, it.px))
    return items
