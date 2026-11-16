import io
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path):
    from paletteai.main import create_app

    app = create_app(data_dir=tmp_path / "data", plugins_dir=ROOT / "plugins")
    with TestClient(app) as c:
        yield c


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def blocks_image(colors, size=40) -> bytes:
    """An image made of equal vertical stripes of the given RGB colours."""
    arr = np.zeros((size, size * len(colors), 3), dtype=np.uint8)
    for i, c in enumerate(colors):
        arr[:, i * size:(i + 1) * size] = c
    return png_bytes(Image.fromarray(arr))


def upload(client, data: bytes, name="test.png", k=None):
    return client.post("/api/v1/images", files={"file": (name, data, "image/png")}, data={"k": k} if k else None)
