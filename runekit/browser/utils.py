import base64
import logging
from typing import TypeVar, List

import cv2
import numpy as np
import pytesseract
# noinspection PyUnresolvedReferences
import runekit._resources
from PIL import Image
from PySide2.QtCore import QPoint, QPropertyAnimation, Property, QAbstractAnimation
from PySide2.QtGui import QColor, QPixmap, QImage
from PySide2.QtWidgets import QGraphicsTextItem
from skimage.metrics import structural_similarity as compare_ssim

from runekit.alt1.schema import RectLike
from runekit.game.instance import ImageType
from runekit.image.np_utils import np_crop

SKIN = ":/runekit/ui/skins/default/fonts/"
TRANSFER_LIMIT = 4_000_000
logger = logging.getLogger(__name__)


class ApiPermissionDeniedException(Exception):
    required_permission: str

    def __init__(self, required_permission: str):
        super().__init__(
            "Permission '%s' is needed for this action".format(required_permission)
        )
        self.required_permission = required_permission


ImgTypeG = TypeVar("T", np.ndarray, Image.Image)


class MQGraphicsTextItem(QGraphicsTextItem):
    animColor: QPropertyAnimation = None
    animSize: QPropertyAnimation = None
    white = QColor.fromRgb(255, 255, 255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def text_color(self) -> QColor:
        return self.defaultTextColor()

    def set_text_color(self, color):
        self.setDefaultTextColor(color)

    def my_scale(self) -> float:
        return self.scale()

    def set_my_scale(self, scale):
        self.setScale(scale)

    color = Property(QColor, text_color, set_text_color)
    text_scale = Property(float, my_scale, set_my_scale)

    def animate(self):
        ocolor = self.text_color()
        self.color = self.white
        self.animColor = QPropertyAnimation(self, b'color')
        self.animColor.setStartValue(self.white)
        self.animColor.setEndValue(ocolor)
        self.animColor.setDuration(500)
        self.animColor.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self.animSize = QPropertyAnimation(self, b'text_scale')
        self.animSize.setStartValue(3)
        self.animSize.setEndValue(1)
        self.animSize.setDuration(500)
        self.animSize.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


class RecursiveNamespace:  # without extending SimpleNamespace!
    @staticmethod
    def map_entry(entry):
        if isinstance(entry, dict):
            return RecursiveNamespace(**entry)

        return entry

    def __init__(self, **kwargs):
        for key, val in kwargs.items():
            if type(val) == dict:
                setattr(self, key, RecursiveNamespace(**val))
            elif type(val) == list:
                setattr(self, key, list(map(self.map_entry, val)))
            else:  # this is the only addition
                setattr(self, key, val)


class RectLikeLetter(RectLike):
    letter: str
    color: List[int]


def ensure_image_rgba(image: ImgTypeG) -> ImgTypeG:
    # XXX: This function is not idempotent!
    if isinstance(image, np.ndarray):
        return image[:, :, [2, 1, 0, 3]]
    else:
        if image.mode == "RGB":
            image = image.convert("RGBA")

        return image


def ensure_image_bgra(image: ImgTypeG) -> ImgTypeG:
    # XXX: This function is not idempotent!
    if isinstance(image, np.ndarray):
        return image
    else:
        if image.mode == "RGB":
            image = image.convert("RGBA")

        r, g, b, a = image.split()
        image = Image.merge("RGBA", (b, g, r, a))

        return image


def ensure_image(image: ImgTypeG, mode: str) -> ImgTypeG:
    if mode == "rgba":
        return ensure_image_rgba(image)
    elif mode == "bgra":
        return ensure_image_bgra(image)
    else:
        raise ValueError("invalid mode")


def image_to_stream(
    image: ImageType,
    x=0,
    y=0,
    width=None,
    height=None,
    mode="bgra",
    ignore_limit=False,
) -> bytes:
    if isinstance(image, np.ndarray):
        out = np_crop(image, x, y, width, height)
        out = ensure_image(out, mode).tobytes()
    else:
        assert image.mode == "RGBA"

        if width is None:
            width = image.width
        if height is None:
            height = image.height

        if not ignore_limit and width * height * 4 > TRANSFER_LIMIT:
            return bytes("")

        image = image.crop((x, y, x + width, y + height))

        out = ensure_image(image, mode).tobytes()

    # debug = True
    # if debug:
    # box_data = read_boxes_from_image(image)
    return out


def read_string_from_image(image):
    ocr = pytesseract.image_to_string(image)
    logger.info(f"ocr:{ocr}")
    return ocr


def read_boxes_from_image(image):
    # ocr = pytesseract.image_to_boxes(image=image, output_type=Output.DICT)
    # logger.info(f"ocr:{ocr}")
    # return ocr
    ocr = pytesseract.image_to_boxes(image).splitlines()
    if not ocr:
        return ocr
    logger.info(f"ocr:{ocr}")

    def torect(a):
        crop = image.crop((int(a[1]), int(a[2])-8, int(a[3]), int(a[4])-6))
        colors = sorted(crop.getcolors())[-1][1]
        color = list(colors)
        rect = RectLikeLetter(letter=a[0], color=color, x=int(a[1]), y=int(a[2]), width=int(a[3]), height=int(a[4]))
        return rect

    rects = list(map(lambda x: torect(list(x.split(' ')[:-1:])), ocr))

    return rects


def encode_mouse(x: int, y: int) -> int:
    return (x << 16) | y


def decode_mouse(x_y: int) -> QPoint:
    return QPoint(x_y >> 16 & 0xff, x_y & 0xff)


def decode_color(color: int) -> QColor:
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = (color >> 0) & 0xFF
    a = (color >> 24) & 0xFF
    return QColor.fromRgb(r, g, b, a)


def decode_image(img: str, width: int) -> np.ndarray:
    img = base64.b64decode(img)
    img = np.frombuffer(img, "<B")
    img.shape = (-1, width, 4)
    return img


def subimg_location(needle: np.ndarray, haystack: np.ndarray) -> tuple:
    needle_local = needle.copy()
    nh, nw, _ = needle_local.shape[::]

    # channels = cv2.split(needle_local)
    # zero_channel = np.zeros_like(channels[0])
    # mask = np.array(channels[3])
    # mask[channels[3] == 0] = 1
    # mask[channels[3] == 255] = 0
    # transparent_mask = cv2.merge([zero_channel, zero_channel, zero_channel, mask])

    haystack_local = haystack.copy()
    hay_local = cv2.cvtColor(haystack_local, cv2.COLOR_BGRA2RGBA)
    # rh, rw, _ = hay_local.shape[::]
    # if rw != w or rh != h:
    #     roi = hay_local[y:y + h, x:x + w]
    # else:
    #     roi = hay_local

    method = cv2.TM_CCOEFF_NORMED
    # for method in [cv2.TM_SQDIFF_NORMED, cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR_NORMED]:
    # for cmask in [transparent_mask, None]:
    result = cv2.matchTemplate(hay_local, needle_local, method, mask=None)

    _min_val, _max_val, min_loc, max_loc = cv2.minMaxLoc(result)

    if method == cv2.TM_SQDIFF_NORMED:
        loc = (_min_val, min_loc)
    else:
        loc = (_max_val, max_loc)

    # potential = np_crop(hay_local, x + loc[1][0], y + loc[1][1], nw, nh)
    potential = np_crop(hay_local, loc[1][0], loc[1][1], nw, nh)
    # logger.info(f"For: {method} {loc[0]}")
    _diff = cv2.subtract(needle_local, potential)
    _absdiff = cv2.subtract(needle_local, potential)
    needle_gray = cv2.cvtColor(needle_local, cv2.COLOR_RGBA2GRAY)
    potential_gray = cv2.cvtColor(potential, cv2.COLOR_RGBA2GRAY)
    if min(nh,nw) < 7:
        _n = min(nh,nw)
        if _n % 2 == 1:
            _ni = _n - 2
        else:
            _ni = _n - 1
        (score, diff) = compare_ssim(needle_gray, potential_gray, full=True, win_size=_ni)
    else:
        (score, diff) = compare_ssim(needle_gray, potential_gray, full=True)

    # diff = (diff * 255).astype("uint8")
    # thresh = cv2.threshold(diff, 0, 255,
    #                        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    # cnts = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL,
    #                         cv2.CHAIN_APPROX_SIMPLE)
    # cnts = imutils.grab_contours(cnts)
    # for c in cnts:
    #     # compute the bounding box of the contour and then draw the
    #     # bounding box on both input images to represent where the two
    #     # images differ
    #     (x, y, w, h) = cv2.boundingRect(c)
    #     cv2.rectangle(needle_local, (x, y), (x + w, y + h), (255, 255, 255, 255), 1)
    #     cv2.rectangle(potential, (x, y), (x + w, y + h), (255, 255, 255, 255), 1)

    # _tmdiff = cv2.merge([potential, transparent_mask])
    dist = cv2.norm(_diff, cv2.NORM_L2)
    absdist = cv2.norm(_absdiff, cv2.NORM_L2SQR)
    # logger.info(f"diff: {diff} dist: {dist}")

    check = (dist, absdist, score)
    # if dist < 500 and absdist < 500:
    logger.info('s: {2:#.02f} d: {0:#.02f} a: {1:#.02f}'.format(*check))

    if score > .90:
        return loc[1], True
    return (), False

