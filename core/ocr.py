"""离线 OCR：读取游戏画面中的文字（RapidOCR + onnxruntime）。"""

import numpy as np

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def read_text(img):
    """识别图片中的文字，返回 [(文字, 中心x, 中心y, 分数), ...]。"""
    if img is None:
        return []
    engine = get_engine()
    result, _ = engine(img)
    items = []
    if not result:
        return items
    for box, text, score in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append((text, int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), float(score)))
    return items


def read_text_boxes(img):
    """识别图片中的文字，返回 [(文字, x0, y0, x1, y1, 分数), ...]（带边框，用于宽度过滤）。"""
    if img is None:
        return []
    engine = get_engine()
    result, _ = engine(img)
    items = []
    if not result:
        return items
    for box, text, score in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append((text,
                      int(min(xs)), int(min(ys)),
                      int(max(xs)), int(max(ys)),
                      float(score)))
    return items
