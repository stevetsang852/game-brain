# Screen refresh flash

Raw GBF1 frames only include changed 16x16 tiles. The dashboard set `canvas.width` on every frame, which clears the bitmap before `getImageData`, so unchanged tiles flashed black.

Fix: resize the canvas only when it is not already 240x160. PNG fallback also avoids a same-size reset.
