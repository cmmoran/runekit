import logging
from typing import TYPE_CHECKING, Callable, Tuple, Dict, List

from PySide2.QtCore import Qt, QRect, QTimer, QPoint
from PySide2.QtGui import QGuiApplication, QPen
from PySide2.QtWidgets import (
    QMainWindow,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsItem,
    QGraphicsRectItem,
)

from .qt import qpixmap_to_np
from ..image import is_color_percent_gte

if TYPE_CHECKING:
    from .instance import GameInstance


class DesktopWideOverlay(QMainWindow):
    _instances: Dict[int, QGraphicsItem]
    view: QGraphicsView
    scene: QGraphicsScene

    def __init__(self):
        super().__init__(
            flags=Qt.Widget | Qt.FramelessWindowHint | Qt.BypassWindowManagerHint | Qt.WindowTransparentForInput | Qt.WindowStaysOnTopHint | Qt.ToolTip
        )
        self.logger = logging.getLogger(__name__ + "." + self.__class__.__name__)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        # self.setStyleSheet("background: rgba(0, 255, 255, 32)")
        self.setStyleSheet("background: transparent")
        self._instances = {}

        # self.scene = QGraphicsScene(QRectF(), parent=self)
        #
        max_w = 0
        max_h = 0
        for screen in QGuiApplication.screens():
            max_w = max(screen.geometry().width(), max_w)
            max_h = max(screen.geometry().height(), max_h)

        self.transparent_pen = QPen()
        self.transparent_pen.setBrush(Qt.NoBrush)

        self.setGeometry(0, 0, max_w, max_h)

    def add_instance(
            self, instance: "GameInstance"
    ) -> Tuple[QGraphicsItem, Callable[[], None]]:
        """Add instance to manage, return a disconnect function and the canvas"""
        def position_changed(rect):
            self.on_instance_moved(instance, rect)

        instance.positionChanged.connect(position_changed)

        def focus_changed(focus):
            self.on_instance_focus_change(instance, focus)

        instance.focusChanged.connect(focus_changed)

        instance_pos = instance.get_position()

        screen = instance.get_screen()

        geom = screen.geometry()
        screen_rect = QRect(0, 0, geom.width(), geom.height())
        self.scene = QGraphicsScene(screen_rect)
        self.view = QGraphicsView(self.scene, self)
        self.view.setScene(self.scene)
        self.view.setSceneRect(screen_rect)
        self.view.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setStyleSheet("background: transparent;")
        # self.view.setStyleSheet("background: rgba(0, 255, 255, 32);")
        self.view.setInteractive(False)
        self.view.setGeometry(screen_rect)
        gfx = QGraphicsRectItem(rect=screen_rect)
        gfx.setPen(self.transparent_pen)
        # q_point = QPoint(instance_pos.x(), instance_pos.y())
        # gfx.setPos(q_point)
        self.scene.addItem(gfx)
        self._instances[instance.wid] = gfx
        self.setGeometry(geom)
        self.logger.info(
            f"onscreen: ({geom.x()},{geom.y()}:{geom.width()} x {geom.height()}) self: ({self.geometry()})  scene: ({self.scene.sceneRect().toRect()}) view:({self.view.geometry()}) gfx:({gfx.scenePos()}")

        def disconnect():
            gfx.hide()
            self.scene.removeItem(gfx)
            instance.positionChanged.disconnect(position_changed)
            instance.focusChanged.disconnect(focus_changed)

        return gfx, disconnect

    def on_instance_focus_change(self, instance, focus):
        self.logger.info(f"Focus:{instance.get_position()} {focus}")
        # self._instances[instance.wid].setVisible(focus)
        pass

    def on_instance_moved(self, instance, pos: QRect):
        self.logger.info(f"Moved: {pos}")
        rect = self._instances[instance.wid]
        rect.setRect(0, 0, pos.width(), pos.height())
        rect.setPos(pos.x(), pos.y())

    def check_compatibility(self):
        QTimer.singleShot(300, self._check_compatibility)

    def _check_compatibility(self):
        # If we cause black screen then hide ourself out of shame...
        screenshot = QGuiApplication.primaryScreen().grabWindow(0)
        image = qpixmap_to_np(screenshot)
        if is_color_percent_gte(image, color=[0, 0, 0], percent=0.95):
            self.logger.warning("Detected black screen condition. Disabling overlay")
            self.hide()
