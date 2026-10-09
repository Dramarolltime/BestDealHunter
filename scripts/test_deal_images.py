#!/usr/bin/env python3
"""Smoke-test deal card rendering without publishing a fake product or deal."""
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from generate_deal_images import generate

with TemporaryDirectory() as tmp:
    folder = Path(tmp)
    source = folder / "illustrative-sample.png"
    output = folder / "sample-card.jpg"
    Image.new("RGB", (640, 480), "#9ca3af").save(source)
    generate({
        "title": "ILLUSTRATIVE TEST PRODUCT — NOT A REAL DEAL",
        "price": 89.99,
        "original_price": 199.99,
    }, source, output)
    assert output.is_file() and output.stat().st_size > 10000
    with Image.open(output) as result:
        assert result.size == (1080, 1080)
        result.verify()
    print("PASS: generated valid 1080x1080 JPEG; no listing or public post changed.")
