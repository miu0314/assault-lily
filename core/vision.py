from pathlib import Path

import cv2
import numpy as np


def decode_png(data):
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def load_image(path):
    return cv2.imread(str(path))


def load_template_with_mask(path):
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None, None
    if img.ndim == 3 and img.shape[2] == 4:
        return img[:, :, :3], img[:, :, 3]
    return img, None


def save_image(img, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return cv2.imwrite(str(path), img)


def crop(img, x, y, w, h):
    return img[y:y + h, x:x + w]


def match_template(screen, template, threshold=0.8):
    if screen is None or template is None:
        return None
    sh, sw = screen.shape[:2]
    th, tw = template.shape[:2]
    if th > sh or tw > sw:
        return None
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if not np.isfinite(max_val) or max_val < threshold:
        return None
    return (max_loc[0] + tw // 2, max_loc[1] + th // 2), float(max_val)


def match_template_masked(screen, template, mask=None, threshold=0.8):
    if screen is None or template is None:
        return None
    sh, sw = screen.shape[:2]
    th, tw = template.shape[:2]
    if th > sh or tw > sw:
        return None
    if mask is not None:
        if mask.shape[:2] != template.shape[:2]:
            mask = cv2.resize(mask, (tw, th))
        result = cv2.matchTemplate(screen, template, cv2.TM_CCORR_NORMED, mask=mask)
    else:
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if not np.isfinite(max_val) or max_val < threshold:
        return None
    return (max_loc[0] + tw // 2, max_loc[1] + th // 2), float(max_val)


def match_template_scale(screen, template, mask=None, threshold=0.7,
                         scales=None, min_scale=0.85, max_scale=1.2):
    if screen is None or template is None:
        return None, None
    sh, sw = screen.shape[:2]
    if scales is None:
        scales = [0.85, 0.9, 1.0, 1.1, 1.2]
    best, best_scale = None, None
    for scale in scales:
        if scale < min_scale or scale > max_scale:
            continue
        tw = max(1, int(template.shape[1] * scale))
        th = max(1, int(template.shape[0] * scale))
        if tw > sw or th > sh:
            continue
        interpolation = cv2.INTER_LINEAR if scale >= 1.0 else cv2.INTER_AREA
        resized = cv2.resize(template, (tw, th), interpolation=interpolation)
        resized_mask = None
        if mask is not None:
            resized_mask = cv2.resize(mask, (tw, th), interpolation=interpolation)
        result = match_template_masked(screen, resized, resized_mask, threshold)
        if result is not None and (best is None or result[1] > best[1]):
            best, best_scale = result, scale
    return best, best_scale


def find_red_dots(img, min_area=5, max_results=50, max_dot_size=40):
    if img is None:
        return []
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 60, 60), (10, 255, 255)),
        cv2.inRange(hsv, (170, 60, 60), (180, 255, 255)),
    )
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    dots = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= min_area and max(w, h) <= max_dot_size:
            dots.append((int(cents[i][0]), int(cents[i][1]), int(area)))
    dots.sort(key=lambda d: -d[2])
    return dots[:max_results]


def find_colored_blob(img, hsv_low, hsv_high, min_area=500):
    if img is None:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, hsv_low, hsv_high)
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= min_area and (best is None or area > best[2]):
            best = (int(cents[i][0]), int(cents[i][1]), int(area))
    return best
