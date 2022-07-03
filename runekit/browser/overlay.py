import base64
import collections
import functools
import logging
import sys
from types import SimpleNamespace
from typing import List, Dict, Tuple, Optional, TYPE_CHECKING, Union, Sequence

from PySide2.QtCore import QObject, QTimer, Qt, QByteArray, QPoint
from PySide2.QtGui import QFont, QPen, QImage, QPixmap, QCursor
from PySide2.QtWidgets import (
    QGraphicsItem,
    QGraphicsDropShadowEffect,
    QGraphicsRectItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
)

from .utils import decode_color, RecursiveNamespace, MQGraphicsTextItem

if TYPE_CHECKING:
    from .api import Alt1Api


def barrier(f):
    """Mark f as sync barrier - all preceeding calls must be finished.
    Only affect calls from scheduler"""
    f.barrier = True
    return f


def ensure_overlay(f):
    def out(self: "OverlayApi", *args, **kwargs):
        if not self.overlay_area:
            return

        return f(self, *args, **kwargs)

    return functools.update_wrapper(out, f)


class OverlayApi(QObject):
    # TODO: I think this could be implemented by QGraphicsItemGroup, maybe even more performant
    current_group: List[str] = []
    groups: Dict[str, Tuple[QGraphicsItemGroup, int]]
    frozen_groups: Dict[str, Tuple[QGraphicsItemGroup, int]]
    queue: List[Tuple[int, str, List]]
    last_call_id: Optional[int] = None
    overlay_area: Optional[QGraphicsItem] = None
    crosshairs: bool = False
    message_model = {}

    def __init__(self, base_api: "Alt1Api", **kwargs):
        super().__init__(**kwargs)
        self.logger = logging.getLogger(__name__)
        self.api = base_api
        self.reset()

        try:
            self.overlay_area = self.api.app.game_instance.get_overlay_area()
        except NotImplementedError:
            pass

    @ensure_overlay
    def enqueue(self, call_id, command, *args):
        # XXX: command is NOT validated. Must be from secure source
        assert command[0] != "_"

        if call_id == 0:
            self.logger.info("Call ID reset")
            self.reset()

        self.queue.append((call_id, command, list(args)))
        self.queue.sort(key=lambda x: x[0])
        self.logger.info("%d %s %s", call_id, command, repr(args)[:180])

        QTimer.singleShot(0, self.process_queue)

    def process_queue(self):
        while len(self.queue) > 0:
            head = self.queue[0]
            f = getattr(self, head[1])
            if (
                    hasattr(f, "barrier")
                    and self.last_call_id is not None
                    and head[0] != self.last_call_id + 1
            ):
                self.logger.info(f"ignore {head[0]} {head[1]} {repr(head[2])[:180]}")
                return

            head = self.queue.pop(0)
            self.last_call_id = head[0]
            try:
                f(*head[2])
            except:
                self.logger.error(
                    "API call exception #%d %s(%s)",
                    head[0],
                    head[1],
                    repr(head[2]),
                    exc_info=True,
                )

    def _finalize_gfx(
            self, sgfx: Union[QGraphicsItem, Sequence[QGraphicsItem]], timeout: int, name: Optional[str] = None
    ):
        if isinstance(sgfx, QGraphicsItem):
            gfx = [sgfx]
        else:
            gfx = sgfx

        if name is None:
            name = self.peek_current_group()

        self.group(name, timeout, gfx)

        def hide():
            try:
                self.hide_group(name)
            except AttributeError:
                pass

        if timeout > 0:
            QTimer.singleShot(timeout, hide)

    def pop_current_group(self):
        if len(self.current_group) == 0:
            return ""
        group = self.current_group[0]
        self.current_group = self.current_group[1::]
        return group

    def peek_current_group(self):
        if len(self.current_group) == 0:
            return ""
        return self.current_group[0]

    def push_current_group(self, name):
        if name in self.current_group:
            self.current_group.remove(name)
        self.current_group.insert(0, name)

    def hide_group(self, name):
        if name in self.groups:
            group, _ = self.groups[name]
            del self.groups[name]
            group.scene().removeItem(group)
            self.api.push("hide-group", name)

    def ungroup(self, name):
        if name in self.groups:
            group, timeout = self.groups[name]
            items = group.childItems()
            del self.groups[name]
        elif name in self.frozen_groups:
            group, timeout = self.frozen_groups[name]
            items = group.childItems()
            del self.frozen_groups[name]
        else:
            return None, -1
        group.scene().destroyItemGroup(group)
        return items, timeout

    def group(self, name, timeout=20000, items=None):
        if name in self.groups or name in self.frozen_groups:
            if name in self.groups:
                group, _ = self.groups[name]
            else:
                group, _ = self.frozen_groups[name]
            if items is not None:
                for item in items:
                    group.addToGroup(item)
                group.scene().update()
            return group
        else:
            if isinstance(items, Sequence):
                group = self.overlay_area.scene().createItemGroup(items)
            else:
                group = self.overlay_area.scene().createItemGroup([items])

        if timeout <= 0:
            self.frozen_groups[name] = (group, 0)
        else:
            self.groups[name] = (group, min(20000, max(timeout, 1)))
        return group

    def reset(self):
        if hasattr(self, "groups"):
            for items, _ in self.groups.values():
                if items.scene():
                    items.scene().removeItem(items)
            self.groups.clear()

        if hasattr(self, "frozen_groups"):
            for items, _ in self.frozen_groups.values():
                if items.scene():
                    items.scene().removeItem(items)
            self.frozen_groups.clear()

        self.groups = collections.defaultdict()
        self.frozen_groups = collections.defaultdict()
        self.queue = []
        self.last_call_id = None

    def update_child_text(self, inner_group, model):
        for child in inner_group.childItems():
            if isinstance(child, MQGraphicsTextItem) and child.data(0):
                current_text = child.toPlainText()
                if child.data(0).format(self=model) != current_text:
                    child.setPlainText(child.data(0).format(self=model))
                    if hasattr(model, "__animate"):
                        child.animate()

    @ensure_overlay
    def overlay_batch(self, commands: List[Tuple[str, List]]):
        for command in commands:
            assert command[0] != "_"

            f = getattr(self, command[0])
            try:
                f(*command[1])
            except:
                self.logger.error(
                    "API call exception #%d %s(%s)",
                    command[0],
                    repr(command[1]),
                    exc_info=True,
                )

    @ensure_overlay
    def overlay_set_group(self, name: str, message_model=None):
        self.push_current_group(name)
        if message_model is not None:
            if isinstance(message_model, dict):
                message_model = RecursiveNamespace(**message_model)
            self.message_model[name] = message_model
            if name in self.frozen_groups:
                group, _ = self.frozen_groups[name]
                self.update_child_text(group, message_model)

    @barrier
    @ensure_overlay
    def overlay_clear_group(self, name: str):
        if name in self.frozen_groups:
            self.overlay_continue_group(name)

        self.hide_group(name)

    @barrier
    @ensure_overlay
    def overlay_freeze_group(self, name: str):
        if name in self.frozen_groups or name not in self.groups:
            self.pop_current_group()
            return

        items, _ = self.ungroup(name)

        self._finalize_gfx(items, 0, name=name)

    @barrier
    @ensure_overlay
    def overlay_continue_group(self, name: str):
        if name in self.groups or name not in self.frozen_groups:
            self.push_current_group(name)
            return

        items, _ = self.ungroup(name)

        self._finalize_gfx(items, 20000, name=name)

    @barrier
    @ensure_overlay
    def overlay_refresh_group(self, name: str):
        if name not in self.frozen_groups:
            return

        self.overlay_continue_group(name)
        self.overlay_freeze_group(name)

    @ensure_overlay
    def overlay_move_group(self, name: str, enable):
        if name not in self.frozen_groups:
            return

        group, _ = self.frozen_groups[name]

        if enable:
            def mover():
                width = int(self.api.get_game_position_width())
                height = int(self.api.get_game_position_height())
                mpos = QCursor.pos()
                npos = QPoint(mpos.x() - self.api.get_game_position_x() - int(width / 2), mpos.y() - self.api.get_game_position_y() - int(height / 2))
                try:
                    if group.pos().x() != npos.x() or group.pos().y() != npos.y():
                        group.setPos(npos.x(), npos.y())
                        if name in self.message_model:
                            model = self.message_model[name]
                            model.mouse_x = npos.x() + int(width / 2)
                            model.mouse_y = npos.y() + int(height / 2)
                        else:
                            model = SimpleNamespace(**{"mouse_x": npos.x() + int(width / 2), "mouse_y": npos.y() + int(height / 2)})
                            self.message_model[name] = model
                        self.update_child_text(group, model)
                except:
                    self.api.mouse_move_signal.disconnect()

            self.api.mouse_move_signal.connect(mover)
        else:
            self.api.mouse_move_signal.disconnect()

    @ensure_overlay
    def overlay_rect(
            self, color: int, x: int, y: int, w: int, h: int, timeout: int, line_width: int
    ):
        pen = QPen(decode_color(color))
        pen.setWidthF(max(1.0, line_width / 10))

        gfx = QGraphicsRectItem(x, y, w, h)
        gfx.setPen(pen)

        self._finalize_gfx(gfx, timeout)

    @ensure_overlay
    def overlay_line(
            self,
            color: int,
            line_width: int,
            x1: int,
            y1: int,
            x2: int,
            y2: int,
            timeout: int,
    ):
        pen = QPen(decode_color(color))
        pen.setWidthF(max(1.0, line_width / 10))

        gfx = QGraphicsLineItem(x1, y1, x2, y2)
        gfx.setPen(pen)

        self._finalize_gfx(gfx, timeout)

    @ensure_overlay
    def overlay_text(
            self,
            message: str,
            color: int,
            size: int,
            x: int,
            y: int,
            timeout: int,
            font_name: str,
            centered: bool,
            shadow: bool,
    ):
        group = self.peek_current_group()
        if group in self.message_model:
            msg_model = self.message_model[group]
            gfx = MQGraphicsTextItem(message.format(self=msg_model))
        else:
            gfx = MQGraphicsTextItem(message)
        gfx.setData(0, message)
        gfx.setDefaultTextColor(decode_color(color))

        if font_name == "" and sys.platform == "darwin":
            # Don't use Helvetica on Mac
            font_name = "Menlo"

        font = QFont(font_name, min(50, size))
        font.setStyleHint(QFont.SansSerif)
        gfx.setFont(font)

        if shadow:
            effect = QGraphicsDropShadowEffect(gfx)
            effect.setBlurRadius(0)
            effect.setColor(Qt.GlobalColor.black)
            effect.setOffset(1, 1)
            gfx.setGraphicsEffect(effect)

        if centered:
            # The provided x, y is at the center of the text
            bound = gfx.boundingRect()
            gfx.setPos(x - (bound.width() / 2), y - (bound.height() / 2))
            xform_point = gfx.mapFromScene(x, y)
            gfx.setTransformOriginPoint(xform_point)
        else:
            bound = gfx.boundingRect()
            gfx.setPos(x, y)
            xform_point = gfx.mapFromScene(x + (bound.width() / 2), y + (bound.height() / 2))
            gfx.setTransformOriginPoint(xform_point)

        self._finalize_gfx(gfx, timeout)

    @ensure_overlay
    def overlay_image(self, img: bytes, x: int, y: int, timeout: int):
        if isinstance(img, str):
            img = base64.b64decode(img)
        img = self.get_qimage(img)

        gfx = QGraphicsPixmapItem(QPixmap.fromImage(img))
        gfx.setPos(x, y)

        self._finalize_gfx(gfx, timeout)

    @ensure_overlay
    def overlay_set_group_z(self, name: str, z_index: int):
        if name not in self.groups:
            return

        item, _ = self.groups[name]
        item.setZValue(z_index)

    @functools.lru_cache(100)
    def get_qimage(self, img: bytes):
        ba = QByteArray(img)
        return QImage.fromData(ba)
