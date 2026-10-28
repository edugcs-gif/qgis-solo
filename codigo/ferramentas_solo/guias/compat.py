# -*- coding: utf-8 -*-
"""Compatibilidade de enums entre QGIS 3 (PyQt5) e QGIS 4 (PyQt6)."""
from qgis.PyQt.QtCore import Qt
from qgis.core import Qgis, QgsEditFormConfig, QgsWkbTypes
from qgis.gui import QgsAttributeEditorContext, QgsRubberBand, QgsVertexMarker


def _pegar(base, *caminhos):
    for c in caminhos:
        v = base
        try:
            for n in c.split("."):
                v = getattr(v, n)
            return v
        except AttributeError:
            continue
    raise AttributeError(caminhos)


class _NS:
    pass


QtC = _NS()
for _nome, _esc in [
    ("CrossCursor", "CursorShape"), ("DashLine", "PenStyle"), ("DotLine", "PenStyle"),
    ("SolidLine", "PenStyle"), ("LeftButton", "MouseButton"), ("RightButton", "MouseButton"),
    ("ShiftModifier", "KeyboardModifier"), ("Key_4", "Key"), ("Key_Backspace", "Key"),
    ("Key_Delete", "Key"), ("Key_Enter", "Key"), ("Key_Escape", "Key"), ("Key_G", "Key"),
    ("Key_D", "Key"), ("Key_I", "Key"), ("Key_M", "Key"), ("Key_P", "Key"), ("Key_Return", "Key"),
    ("Key_Up", "Key"), ("Key_Down", "Key"),
    ("RoundJoin", "PenJoinStyle"), ("AlignLeft", "AlignmentFlag"),
]:
    setattr(QtC, _nome, _pegar(Qt, _esc + "." + _nome, _nome))

# dicas de renderização do QPainter (Qt5: QPainter.Antialiasing / Qt6: QPainter.RenderHint.Antialiasing)
from qgis.PyQt.QtGui import QPainter as _QPainter  # noqa: E402
AA = _pegar(_QPainter, "RenderHint.Antialiasing", "Antialiasing")
TEXT_AA = _pegar(_QPainter, "RenderHint.TextAntialiasing", "TextAntialiasing")

VM = _NS()
for _nome in ("ICON_BOX", "ICON_CIRCLE", "ICON_CROSS", "ICON_TRIANGLE", "ICON_X"):
    setattr(VM, _nome, _pegar(QgsVertexMarker, "IconType." + _nome, _nome))

RB_CIRCULO = _pegar(QgsRubberBand, "IconType.ICON_CIRCLE", "ICON_CIRCLE")
NIVEL_INFO = _pegar(Qgis, "MessageLevel.Info", "Info")
NIVEL_AVISO = _pegar(Qgis, "MessageLevel.Warning", "Warning")
PONTO = _pegar(Qgis, "GeometryType.Point") if hasattr(Qgis, "GeometryType") else QgsWkbTypes.PointGeometry
LINHA = _pegar(Qgis, "GeometryType.Line") if hasattr(Qgis, "GeometryType") else QgsWkbTypes.LineGeometry
POLIGONO = _pegar(Qgis, "GeometryType.Polygon") if hasattr(Qgis, "GeometryType") else QgsWkbTypes.PolygonGeometry
FORM_SUPRIMIR = _pegar(Qgis, "AttributeFormSuppression.On") if hasattr(Qgis, "AttributeFormSuppression") \
    else QgsEditFormConfig.SuppressOn
FORM_PADRAO = _pegar(Qgis, "AttributeFormSuppression.Default") if hasattr(Qgis, "AttributeFormSuppression") \
    else QgsEditFormConfig.SuppressDefault
MODO_ADICIONAR = _pegar(QgsAttributeEditorContext, "Mode.AddFeatureMode", "AddFeatureMode")


def pos_evento(e):
    """Posição do mouse em pixels (QPoint) em Qt5 e Qt6."""
    try:
        return e.pos()
    except AttributeError:
        return e.position().toPoint()
