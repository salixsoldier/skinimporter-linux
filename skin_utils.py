"""
skin_utils.py — skin image validation and base64 encoding helpers.

No changes needed for Linux compatibility; this file was already
platform-agnostic.  Kept identical to the original.
"""

import base64
import io
from PIL import Image

EXPECTED_SKIN_SIZE = (64, 32)
CONVERTIBLE_SKIN_SIZE = (64, 64)


def get_skin_size(image_path: str) -> tuple[int, int]:
    with Image.open(image_path) as image:
        return image.size


def is_skin_ratio_2_to_1(image_path: str) -> bool:
    width, height = get_skin_size(image_path)
    return height > 0 and width == height * 2


def is_skin_size_64x32(image_path: str) -> bool:
    return get_skin_size(image_path) == EXPECTED_SKIN_SIZE


def is_skin_size_64x64(image_path: str) -> bool:
    return get_skin_size(image_path) == CONVERTIBLE_SKIN_SIZE


def is_valid_skin_size(image_path: str) -> bool:
    return is_skin_size_64x32(image_path) or is_skin_ratio_2_to_1(image_path)


def convert_skin_64x64_to_64x32_bytes(image_path: str) -> bytes:
    if not is_skin_size_64x64(image_path):
        raise ValueError("Skin must be 64x64 to convert")

    with Image.open(image_path) as source_image:
        converted_image = Image.new("RGBA", EXPECTED_SKIN_SIZE)
        box = (0, 0, EXPECTED_SKIN_SIZE[0], EXPECTED_SKIN_SIZE[1])
        converted_image.paste(source_image.crop(box), box)

    output = io.BytesIO()
    converted_image.save(output, format="PNG")
    return output.getvalue()


def get_import_ready_skin_base64(image_path: str) -> str:
    if is_skin_size_64x64(image_path):
        image_bytes = convert_skin_64x64_to_64x32_bytes(image_path)
    else:
        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()

    return base64.b64encode(image_bytes).decode("ascii")
