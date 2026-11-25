"""Built-in sample images.

Most are drawn here in code. The IIT Kanpur photos ship inside the package
(paletteai/sample_photos/, cropped to 480x320 from Wikimedia Commons; credits in the
README), so the demo still needs no downloads.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SAMPLES = {
    "sunset": ("Sunset", "Warm gradient sky with dark hills"),
    "forest": ("Forest", "Layered greens with a pale sky"),
    "ocean": ("Ocean", "Blue water bands meeting sand"),
    "poster": ("Poster", "Flat five-colour geometric poster"),
    "logo": ("Logo", "Shapes on a transparent background"),
    "mono": ("Monochrome", "Smooth grayscale gradient"),
    "desert": ("Desert", "Sand dunes under a hazy peach sky"),
    "neon": ("Neon", "Glowing shapes on a night-blue background"),
    "pastel": ("Pastel", "Soft blocks with low saturation"),
    "iitk_library": ("IITK Library", "P.K. Kelkar Library · photo: Lalujbad, CC BY-SA 3.0"),
    "iitk_computer_centre": ("IITK Computer Centre", "Red brick and bougainvillea · photo: Dr Vinamra, CC BY-SA 4.0"),
    "iitk_hall2": ("IITK Hall 2", "Lawn with peacocks · photo: Vaibhav.jndl, CC BY-SA 4.0"),
}
PHOTO_DIR = Path(__file__).resolve().parent / "sample_photos"
W, H = 480, 320


def _gradient(stops: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    ys = np.linspace(0, 1, H)
    pos = [p for p, _ in stops]
    cols = np.array([c for _, c in stops], dtype=np.float64)
    column = np.stack([np.interp(ys, pos, cols[:, i]) for i in range(3)], axis=1)
    return np.repeat(column[:, None, :], W, axis=1)


def _noisy(arr: np.ndarray, rng: np.random.Generator, amount: float = 6) -> Image.Image:
    arr = arr + rng.normal(0, amount, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _hills(draw: ImageDraw.ImageDraw, base: int, amp: int, period: float, phase: float, fill) -> None:
    pts = [(x, base + amp * np.sin(x / period + phase)) for x in range(0, W + 8, 8)]
    draw.polygon([(0, H)] + pts + [(W, H)], fill=fill)


def _make(name: str) -> Image.Image:
    photo = PHOTO_DIR / f"{name}.jpg"
    if photo.exists():
        with Image.open(photo) as im:
            return im.convert("RGB")
    rng = np.random.default_rng(sum(map(ord, name)))
    if name == "sunset":
        im = _noisy(_gradient([(0, (58, 36, 110)), (0.45, (214, 84, 96)), (0.7, (247, 150, 70)), (1, (252, 214, 120))]), rng, 3)
        d = ImageDraw.Draw(im)
        d.ellipse((300, 150, 380, 230), fill=(255, 236, 170))
        _hills(d, 250, 18, 46, 0.5, (58, 30, 66))
        _hills(d, 285, 14, 30, 2.0, (28, 18, 40))
        return im
    if name == "forest":
        im = _noisy(_gradient([(0, (214, 232, 226)), (0.4, (190, 216, 196)), (1, (150, 190, 150))]), rng, 3)
        d = ImageDraw.Draw(im)
        for base, amp, per, ph, col in [(150, 22, 50, 0, (112, 160, 116)), (200, 20, 36, 1.3, (62, 122, 78)),
                                        (245, 16, 28, 2.4, (34, 86, 58)), (290, 10, 22, 4.0, (18, 52, 40))]:
            _hills(d, base, amp, per, ph, col)
        return _noisy(np.asarray(im, dtype=np.float64), rng, 4)
    if name == "ocean":
        return _noisy(_gradient([(0, (10, 52, 104)), (0.35, (18, 104, 160)), (0.6, (60, 170, 190)),
                                 (0.72, (170, 222, 214)), (0.78, (236, 222, 184)), (1, (214, 186, 140))]), rng, 5)
    if name == "poster":
        im = Image.new("RGB", (W, H), (245, 239, 224))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 190, H), fill=(28, 52, 84))
        d.ellipse((110, 60, 310, 260), fill=(226, 86, 60))
        d.polygon([(260, 320), (480, 120), (480, 320)], fill=(240, 180, 50))
        d.rectangle((330, 30, 450, 90), fill=(44, 142, 132))
        return im
    if name == "logo":
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((90, 50, 310, 270), fill=(98, 80, 214, 255))
        d.rounded_rectangle((220, 120, 400, 250), radius=28, fill=(27, 175, 122, 255))
        d.ellipse((180, 130, 260, 210), fill=(250, 250, 250, 255))
        return im
    if name == "mono":
        xs = np.linspace(20, 235, W)
        return _noisy(np.repeat(np.repeat(xs[None, :, None], H, axis=0), 3, axis=2), rng, 2)
    if name == "desert":
        im = _noisy(_gradient([(0, (150, 190, 214)), (0.35, (236, 206, 182)), (0.55, (248, 222, 190)), (1, (240, 200, 150))]), rng, 3)
        d = ImageDraw.Draw(im)
        for base, amp, per, ph, col in [(190, 24, 70, 0.3, (226, 170, 104)), (235, 20, 52, 2.1, (204, 132, 72)),
                                        (282, 14, 40, 3.6, (164, 96, 52))]:
            _hills(d, base, amp, per, ph, col)
        return _noisy(np.asarray(im, dtype=np.float64), rng, 4)
    if name == "neon":
        im = _noisy(_gradient([(0, (14, 16, 46)), (1, (30, 14, 56))]), rng, 3)
        d = ImageDraw.Draw(im)
        for box, col in [((60, 70, 210, 220), (255, 46, 160)), ((250, 40, 420, 210), (0, 220, 255))]:
            for w, a in [(14, .25), (8, .5), (3, 1.0)]:      # soft glow, then a bright core
                d.ellipse(box, outline=tuple(int(c * a + 20 * (1 - a)) for c in col), width=w)
        d.line((40, 270, 440, 270), fill=(255, 230, 60), width=6)
        return im
    if name == "pastel":
        im = Image.new("RGB", (W, H), (250, 246, 240))
        d = ImageDraw.Draw(im)
        cols = [(246, 196, 200), (190, 222, 236), (204, 232, 198), (250, 226, 170), (214, 200, 238), (248, 212, 184)]
        for i, col in enumerate(cols):
            x, y = 24 + (i % 3) * 148, 24 + (i // 3) * 140
            d.rounded_rectangle((x, y, x + 132, y + 124), radius=18, fill=col)
        return im
    raise KeyError(name)


def ensure_samples(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in SAMPLES:
        path = folder / f"{name}.png"
        if not path.exists():
            _make(name).save(path, "PNG")


def list_samples() -> list[dict]:
    return [{"id": k, "name": title, "description": desc} for k, (title, desc) in SAMPLES.items()]
