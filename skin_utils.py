from PIL import Image

EXPECTED_SKIN_SIZE = (64, 32)


def get_skin_size(image_path: str) -> tuple[int, int]:
    with Image.open(image_path) as image:
        return image.size


def is_skin_ratio_2_to_1(image_path: str) -> bool:
    width, height = get_skin_size(image_path)
    return height > 0 and width == height * 2


def is_skin_size_64x32(image_path: str) -> bool:
    return get_skin_size(image_path) == EXPECTED_SKIN_SIZE


def is_valid_skin_size(image_path: str) -> bool:
    return is_skin_size_64x32(image_path) or is_skin_ratio_2_to_1(image_path)
