"""Shared red-health geometry, excluding minimap and friendly resource bars."""
import cv2
import numpy as np


def enemy_bars(picture):
    hsv = cv2.cvtColor(np.asarray(picture.convert('RGB')), cv2.COLOR_RGB2HSV)
    unit = picture.height/576
    red = (((hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 168)) &
           (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 65)).astype(np.uint8)*255
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((1, max(1, round(3*unit))), np.uint8))
    red = cv2.morphologyEx(red, cv2.MORPH_OPEN, np.ones((max(1, round(3*unit)), max(3, round(25*unit))), np.uint8))
    contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bars = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (25*unit <= w <= 145*unit and 2*unit <= h <= 18*unit and
                15*unit <= y < 495*unit and x >= 25*unit and cv2.contourArea(contour) > w*h*.45):
            continue
        if x < picture.width*.22 and y < 200*unit:
            continue
        if h > 12*unit:
            # Selected enemies have an orange rim joined to the red fill.
            # Its top edge is not the level-digit anchor. A real level ring
            # must corroborate the thicker contour before normalizing it.
            from gameplan.vision.health_nameplates import level_circle
            anchor = y + h//2 - max(1, round(unit))
            normalized = picture if unit == 1 else picture.resize((round(picture.width/unit), 576))
            if not level_circle(normalized, round(x/unit), round(anchor/unit)):
                continue
            y, h = anchor, max(3, round(6*unit))
        above = hsv[max(0, y-round(13*unit)):y, x:x+w]
        friendly = ((above[:, :, 0] >= 35) & (above[:, :, 0] <= 125) &
                    (above[:, :, 1] > 90) & (above[:, :, 2] > 90))
        if h <= 6*unit and len(friendly) and (friendly.mean(axis=1) > .35).sum() >= max(2, round(2*unit)):
            continue
        bars.append([x, y, w, h])
    return sorted(bars, key=lambda b: -b[2])[:12]
