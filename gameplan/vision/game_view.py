"""Remove only edge-connected AirDroid chrome/letterboxing before OCR."""
import numpy as np


def game_view(picture):
    pixels = np.asarray(picture.convert('RGB'))
    height, width = pixels.shape[:2]
    left, top, right, bottom = 0, 0, width, height
    def margin(line):
        high, low = line.max(axis=1), line.min(axis=1)
        # AirDroid's white toolbar has a neutral gray drop shadow at its outer
        # edge. Include that connected shadow or trimming stops before the UI.
        return ((low > 175) & (high-low < 30)).mean() > .82 or (high < 12).mean() > .985
    # White toolbar icons may interrupt a column. A game image must still be
    # at least 80% of the source in each direction; no interior rectangles.
    while top < height*.10 and margin(pixels[top, left:right]):
        top += 1
    while bottom > height*.92 and margin(pixels[bottom-1, left:right]):
        bottom -= 1
    while left < width*.10 and margin(pixels[top:bottom, left]):
        left += 1
    while right > width*.95 and margin(pixels[top:bottom, right-1]):
        right -= 1
    if right-left < width*.8 or bottom-top < height*.8:
        return picture
    return picture.crop((left, top, right, bottom))
