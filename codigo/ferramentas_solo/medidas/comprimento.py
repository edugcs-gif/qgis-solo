# -*- coding: utf-8 -*-
"""
Comprimento e Área (parte do Ferramentas Solo)

Menção honrosa: baseado no complemento "Comprimento e Área" (Comprimento de
Segmentos e Área de Polígonos) de Joel Victor Feitosa da Silva — toda a lógica de
medir e desenhar os comprimentos e áreas no mapa vem do trabalho dele.
Integração ao Ferramentas Solo: Edu Gabriel Castilho Sembalista.
Desenha sobre o mapa o comprimento de cada segmento e/ou a área dos polígonos
das camadas em edição ou ativas.

Um único botão na barra "Ferramentas Solo":
  - clique no botão  -> liga/desliga as medições no mapa
  - seta ao lado     -> escolhe o que mostrar (segmentos e área, só segmentos,
                        só área) e as demais opções

As opções de casas decimais, vírgula, fonte e elipsoidal também valem para as
medidas ao vivo das ferramentas de desenhar/mover vértice (ver ao_vivo.py).
"""
import math
import os
from functools import partial

from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetricsF, QIcon, QPainterPath, QPen
from qgis.PyQt.QtWidgets import QInputDialog, QMenu, QToolButton

try:
    from qgis.PyQt.QtWidgets import QAction, QActionGroup
except ImportError:  # Qt6
    from qgis.PyQt.QtGui import QAction, QActionGroup

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeatureRequest,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsSettings,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..guias.compat import AA, LINHA, NIVEL_AVISO, POLIGONO, TEXT_AA, QtC

KEY = "ComprimentoSegmentos/"      # mesma chave do complemento antigo: as opções salvas continuam valendo
MENU = "&Ferramentas Solo"
TITLE = "Comprimento e Área"
ICON_PATH = os.path.join(os.path.dirname(__file__), "icon.svg")
MAX_FEATURES = 3000
MAX_LABELS = 20000

# (chave, texto no menu, texto curto para a dica do botão)
MODES = (
    ("ambos", "Mostrar segmentos e área", "segmentos e área"),
    ("segmentos", "Mostrar somente segmentos (distância)", "somente segmentos"),
    ("area", "Mostrar somente área (polígonos)", "somente área"),
)
MODE_KEYS = tuple(m[0] for m in MODES)


def _meters():
    try:
        return Qgis.DistanceUnit.Meters
    except AttributeError:
        return QgsUnitTypes.DistanceMeters


def _sq_meters():
    try:
        return Qgis.AreaUnit.SquareMeters
    except AttributeError:
        return QgsUnitTypes.AreaSquareMeters


def _reverse_direction():
    try:
        return Qgis.TransformDirection.Reverse
    except AttributeError:
        return QgsCoordinateTransform.ReverseTransform


def _menu_button_popup():
    try:
        return QToolButton.ToolButtonPopupMode.MenuButtonPopup
    except AttributeError:
        return QToolButton.MenuButtonPopup


def formatar(valor, decimais, virgula):
    texto = "{:.{d}f}".format(valor, d=decimais)
    return texto.replace(".", ",") if virgula else texto


class ComprimentoSegmentos:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.toolbar = toolbar
        self.menu_nome = menu or MENU
        self.settings = QgsSettings()
        self.actions = []
        self.hooked = set()
        self.menu = None
        self.mode_group = None
        self.act_toggle = None
        # (id da camada, id da feição) que está sendo editada com medidas ao vivo:
        # o rótulo fixo dela some enquanto o rótulo ao vivo está na tela
        self.excluir = None

        if self.settings.contains(KEY + "enabled"):
            self.enabled = self._get("enabled", True)
            mode = self.settings.value(KEY + "mode", "ambos", type=str)
        else:
            # Migra a configuração da versão antiga (dois botões separados)
            seg = self._get("show_segments", True)
            area = self._get("show_area", True)
            self.enabled = seg or area
            if seg and not area:
                mode = "segmentos"
            elif area and not seg:
                mode = "area"
            else:
                mode = "ambos"
        self.mode = mode if mode in MODE_KEYS else "ambos"

        self.only_selected = self._get("only_selected", False)
        self.always = self._get("always", False)
        self.ellipsoidal = self._get("ellipsoidal", False)
        self.comma = self._get("comma", True)
        self.decimals = int(self.settings.value(KEY + "decimals", 2))
        self.font_size = float(self.settings.value(KEY + "font_size", 9.0))

    # O que desenhar depende do botão (ligado/desligado) e do modo escolhido
    @property
    def show_segments(self):
        return self.enabled and self.mode in ("ambos", "segmentos")

    @property
    def show_area(self):
        return self.enabled and self.mode in ("ambos", "area")

    # ------------------------------------------------------------------
    # Configurações
    # ------------------------------------------------------------------
    def _get(self, name, default):
        return self.settings.value(KEY + name, default, type=bool)

    def _set(self, name, value):
        self.settings.setValue(KEY + name, value)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def _add_action(self, text, callback, checkable=False, checked=False, group=None):
        """Cria uma opção no menu da seta do botão (o mesmo menu aparece em
        Vetor > Ferramentas Solo > Opções de Comprimento e Área)."""
        act = QAction(text, self.iface.mainWindow())
        if checkable:
            act.setCheckable(True)
            act.setChecked(checked)
        if group is not None:
            group.addAction(act)
            act.triggered.connect(callback)
        elif checkable:
            act.toggled.connect(callback)
        else:
            act.triggered.connect(callback)
        self.menu.addAction(act)
        self.actions.append(act)
        return act

    def initGui(self):
        mw = self.iface.mainWindow()

        # Botão principal: liga/desliga
        self.act_toggle = QAction(QIcon(ICON_PATH), TITLE, mw)
        self.act_toggle.setObjectName("mFerramentasSoloComprimentoArea")
        self.act_toggle.setCheckable(True)
        self.act_toggle.setChecked(self.enabled)
        self.act_toggle.toggled.connect(self.on_toggle)

        # Menu da seta do botão
        self.menu = QMenu("Opções de Comprimento e Área", mw)
        self.mode_group = QActionGroup(mw)
        self.mode_group.setExclusive(True)
        for key, label, _short in MODES:
            self._add_action(label, partial(self.on_mode, key), True,
                             key == self.mode, group=self.mode_group)
        self.menu.addSeparator()
        self._add_action("Somente feições selecionadas", self.on_only_selected,
                         True, self.only_selected)
        self._add_action("Mostrar também fora do modo de edição (camada ativa)", self.on_always,
                         True, self.always)
        self._add_action("Usar distância/área elipsoidal", self.on_ellipsoidal,
                         True, self.ellipsoidal)
        self._add_action("Usar vírgula como separador decimal", self.on_comma,
                         True, self.comma)
        self.menu.addSeparator()
        self._add_action("Casas decimais...", self.ask_decimals)
        self._add_action("Tamanho da fonte...", self.ask_font_size)

        if self.toolbar is not None:
            self.toolbar.addAction(self.act_toggle)
            button = self.toolbar.widgetForAction(self.act_toggle)
        else:
            self.iface.addToolBarIcon(self.act_toggle)
            button = self.iface.pluginToolBar().widgetForAction(self.act_toggle)
        # clique = liga/desliga, seta = menu de opções
        if isinstance(button, QToolButton):
            button.setMenu(self.menu)
            button.setPopupMode(_menu_button_popup())

        self.iface.addPluginToVectorMenu(self.menu_nome, self.act_toggle)
        self.iface.addPluginToVectorMenu(self.menu_nome, self.menu.menuAction())

        self._update_tooltip()

        self.canvas.renderComplete.connect(self.on_render_complete)

        project = QgsProject.instance()
        project.layersAdded.connect(self._hook_layers)
        self._hook_layers(list(project.mapLayers().values()))
        self.canvas.refresh()

    def unload(self):
        try:
            self.canvas.renderComplete.disconnect(self.on_render_complete)
        except Exception:
            pass

        project = QgsProject.instance()
        try:
            project.layersAdded.disconnect(self._hook_layers)
        except Exception:
            pass

        for lid in list(self.hooked):
            layer = project.mapLayer(lid)
            if layer is not None:
                self._unhook_layer(layer)
        self.hooked.clear()

        if self.menu is not None:
            self.iface.removePluginVectorMenu(self.menu_nome, self.menu.menuAction())
        if self.act_toggle is not None:
            if self.toolbar is not None:
                try:
                    self.toolbar.removeAction(self.act_toggle)
                except RuntimeError:
                    pass
            else:
                self.iface.removeToolBarIcon(self.act_toggle)
            self.iface.removePluginVectorMenu(self.menu_nome, self.act_toggle)
            self.act_toggle.deleteLater()
            self.act_toggle = None

        for act in self.actions:
            act.deleteLater()
        self.actions = []

        if self.mode_group is not None:
            self.mode_group.deleteLater()
            self.mode_group = None
        if self.menu is not None:
            self.menu.deleteLater()
            self.menu = None
        self.excluir = None
        self.canvas.refresh()

    # ------------------------------------------------------------------
    # Feição em edição com medidas ao vivo (o rótulo fixo dela é escondido)
    # ------------------------------------------------------------------
    def definir_excluida(self, chave):
        if chave == self.excluir:
            return
        self.excluir = chave
        if self.show_segments or self.show_area:
            self.canvas.refresh()

    # ------------------------------------------------------------------
    # Reação a eventos
    # ------------------------------------------------------------------
    def _hook_layers(self, layers):
        for layer in layers:
            if not isinstance(layer, QgsVectorLayer) or layer.id() in self.hooked:
                continue
            layer.editingStarted.connect(self._refresh)
            layer.editingStopped.connect(self._refresh)
            layer.selectionChanged.connect(self._on_selection)
            self.hooked.add(layer.id())

    def _unhook_layer(self, layer):
        for sig, slot in ((layer.editingStarted, self._refresh),
                          (layer.editingStopped, self._refresh),
                          (layer.selectionChanged, self._on_selection)):
            try:
                sig.disconnect(slot)
            except Exception:
                pass

    def _refresh(self, *args):
        self.canvas.refresh()

    def _on_selection(self, *args):
        if self.only_selected and (self.show_segments or self.show_area):
            self.canvas.refresh()

    def _update_tooltip(self):
        if self.act_toggle is None:
            return
        short = next(s for k, _l, s in MODES if k == self.mode)
        state = "Ligado" if self.enabled else "Desligado"
        self.act_toggle.setToolTip(
            "{} — {} ({})\nClique para ligar/desligar · seta para opções".format(TITLE, state, short))

    def on_toggle(self, checked):
        self.enabled = checked
        self._set("enabled", checked)
        self._update_tooltip()
        self.canvas.refresh()

    def on_mode(self, key, checked=True):
        self.mode = key
        self._set("mode", key)
        if not self.enabled:
            # Escolher o que mostrar já liga o botão
            self.act_toggle.setChecked(True)
        self._update_tooltip()
        self.canvas.refresh()

    def on_only_selected(self, checked):
        self.only_selected = checked
        self._set("only_selected", checked)
        self.canvas.refresh()

    def on_always(self, checked):
        self.always = checked
        self._set("always", checked)
        self.canvas.refresh()

    def on_ellipsoidal(self, checked):
        self.ellipsoidal = checked
        self._set("ellipsoidal", checked)
        self.canvas.refresh()

    def on_comma(self, checked):
        self.comma = checked
        self._set("comma", checked)
        self.canvas.refresh()

    def ask_decimals(self):
        value, ok = QInputDialog.getInt(
            self.iface.mainWindow(), "Casas decimais",
            "Número de casas decimais:", self.decimals, 0, 6)
        if ok:
            self.decimals = value
            self._set("decimals", value)
            self.canvas.refresh()

    def ask_font_size(self):
        value, ok = QInputDialog.getDouble(
            self.iface.mainWindow(), "Tamanho da fonte",
            "Tamanho da fonte (pt):", self.font_size, 5.0, 30.0, 1)
        if ok:
            self.font_size = value
            self._set("font_size", value)
            self.canvas.refresh()

    # ------------------------------------------------------------------
    # Desenho no Canvas
    # ------------------------------------------------------------------
    def on_render_complete(self, painter):
        if not (self.show_segments or self.show_area):
            return
        try:
            self._draw(painter)
        except Exception as exc:
            QgsMessageLog.logMessage(
                "Erro ao desenhar medições: {}".format(exc),
                "Ferramentas Solo", NIVEL_AVISO)

    def _target_layers(self):
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        if self.always:
            active = self.iface.activeLayer()
            candidates = [active] if active is not None else []
        else:
            candidates = [l for l in project.mapLayers().values()
                          if isinstance(l, QgsVectorLayer) and l.isEditable()]

        result = []
        for layer in candidates:
            if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
                continue
            if layer.geometryType() not in (LINHA, POLIGONO):
                continue
            node = root.findLayer(layer.id())
            if node is None or not node.isVisible():
                continue
            result.append(layer)
        return result

    @staticmethod
    def _rings(geom, is_polygon):
        out = []
        multi = QgsWkbTypes.isMultiType(geom.wkbType())
        if is_polygon:
            polys = geom.asMultiPolygon() if multi else [geom.asPolygon()]
            for poly in polys:
                for i, ring in enumerate(poly):
                    out.append((ring, i > 0))
        else:
            lines = geom.asMultiPolyline() if multi else [geom.asPolyline()]
            for line in lines:
                out.append((line, False))
        return out

    def _draw(self, painter):
        layers = self._target_layers()
        if not layers:
            return

        project = QgsProject.instance()
        ctx = project.transformContext()
        dest_crs = self.canvas.mapSettings().destinationCrs()
        m2p = self.canvas.getCoordinateTransform()
        extent = self.canvas.extent()
        cw, ch = self.canvas.width(), self.canvas.height()

        font = QFont()
        font.setPointSizeF(self.font_size)
        font.setBold(True)
        fm = QFontMetricsF(font)
        offset = fm.height() / 2.0 + 3.0

        halo = QPen(QColor(255, 255, 255, 230))
        halo.setWidthF(3.0)
        halo.setJoinStyle(QtC.RoundJoin)

        fill_seg = QBrush(QColor(20, 20, 20))
        fill_area = QBrush(QColor(0, 102, 204))  # Azul destacado para a área central

        painter.save()
        painter.setRenderHint(AA, True)
        painter.setRenderHint(TEXT_AA, True)

        drawn = 0
        try:
            for layer in layers:
                to_canvas = QgsCoordinateTransform(layer.crs(), dest_crs, ctx)

                req = QgsFeatureRequest()
                req.setLimit(MAX_FEATURES)
                if self.only_selected:
                    req.setFilterFids(layer.selectedFeatureIds())
                else:
                    try:
                        req.setFilterRect(to_canvas.transformBoundingBox(extent, _reverse_direction()))
                    except Exception:
                        pass

                use_ellips = self.ellipsoidal or layer.crs().isGeographic()
                da = QgsDistanceArea()
                da.setSourceCrs(layer.crs(), ctx)
                da.setEllipsoid(project.ellipsoid())
                if use_ellips:
                    len_factor = QgsUnitTypes.fromUnitToUnitFactor(da.lengthUnits(), _meters())
                    area_factor = QgsUnitTypes.fromUnitToUnitFactor(da.areaUnits(), _sq_meters())
                else:
                    len_factor = QgsUnitTypes.fromUnitToUnitFactor(layer.crs().mapUnits(), _meters())
                    area_factor = len_factor * len_factor

                is_polygon = layer.geometryType() == POLIGONO
                excl = self.excluir if (self.excluir and self.excluir[0] == layer.id()) else None

                for feat in layer.getFeatures(req):
                    if excl is not None and feat.id() == excl[1]:
                        continue  # está com medida ao vivo (ferramenta de mover vértice)
                    geom = feat.geometry()
                    if geom is None or geom.isNull() or geom.isEmpty():
                        continue
                    if QgsWkbTypes.isCurvedType(geom.wkbType()):
                        geom = QgsGeometry(geom)
                        geom.convertToStraightSegment()

                    # --- 1. DESENHO DA ÁREA (Se ativo e for polígono) ---
                    if self.show_area and is_polygon:
                        if use_ellips:
                            area_val = da.measureArea(geom) * area_factor
                        else:
                            area_val = geom.area() * area_factor

                        centroid = geom.centroid()
                        if not centroid.isEmpty():
                            c_pt = centroid.asPoint()
                            try:
                                mp_c = to_canvas.transform(c_pt)
                            except Exception:
                                mp_c = c_pt
                            pix_c = m2p.transform(mp_c)

                            if -50 <= pix_c.x() <= cw + 50 and -50 <= pix_c.y() <= ch + 50:
                                area_str = "{:.{d}f} m²".format(area_val, d=self.decimals)
                                if self.comma:
                                    area_str = area_str.replace(".", ",")
                                tw_a = fm.horizontalAdvance(area_str)

                                painter.save()
                                painter.translate(pix_c.x(), pix_c.y())
                                path_a = QPainterPath()
                                path_a.addText(-tw_a / 2.0, (fm.ascent() - fm.descent()) / 2.0, font, area_str)
                                painter.strokePath(path_a, halo)
                                painter.fillPath(path_a, fill_area)
                                painter.restore()
                                drawn += 1

                    # --- 2. DESENHO DOS SEGMENTOS (Se ativo) ---
                    if self.show_segments:
                        for ring, is_hole in self._rings(geom, is_polygon):
                            if len(ring) < 2:
                                continue

                            pix = []
                            for pt in ring:
                                try:
                                    mp = to_canvas.transform(QgsPointXY(pt))
                                except Exception:
                                    mp = QgsPointXY(pt)
                                pix.append(m2p.transform(mp))

                            area_screen = 0.0
                            for i in range(len(pix) - 1):
                                area_screen += pix[i].x() * pix[i + 1].y() - pix[i + 1].x() * pix[i].y()
                            sgn = 1.0 if area_screen > 0 else -1.0
                            if is_hole:
                                sgn = -sgn

                            for i in range(len(ring) - 1):
                                a, b = ring[i], ring[i + 1]
                                if use_ellips:
                                    dist = da.measureLine(QgsPointXY(a), QgsPointXY(b)) * len_factor
                                else:
                                    dist = math.hypot(b.x() - a.x(), b.y() - a.y()) * len_factor
                                if dist <= 0:
                                    continue

                                p1, p2 = pix[i], pix[i + 1]
                                dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
                                plen = math.hypot(dx, dy)
                                if plen < 1.0:
                                    continue

                                mx, my = (p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0
                                if mx < -50 or my < -50 or mx > cw + 50 or my > ch + 50:
                                    continue

                                text = "{:.{d}f}".format(dist, d=self.decimals)
                                if self.comma:
                                    text = text.replace(".", ",")
                                tw = fm.horizontalAdvance(text)
                                if plen < tw + 8:
                                    continue  # Não desenha se o texto for maior que o lado na tela

                                ux, uy = dx / plen, dy / plen
                                if is_polygon:
                                    ox, oy = uy * sgn, -ux * sgn
                                else:
                                    ox, oy = uy, -ux
                                    if oy > 0:
                                        ox, oy = -ox, -oy

                                angle = math.degrees(math.atan2(dy, dx))
                                if angle > 90:
                                    angle -= 180
                                elif angle < -90:
                                    angle += 180

                                painter.save()
                                painter.translate(mx + ox * offset, my + oy * offset)
                                painter.rotate(angle)
                                path = QPainterPath()
                                path.addText(-tw / 2.0, (fm.ascent() - fm.descent()) / 2.0, font, text)
                                painter.strokePath(path, halo)
                                painter.fillPath(path, fill_seg)
                                painter.restore()

                                drawn += 1
                                if drawn >= MAX_LABELS:
                                    return
        finally:
            painter.restore()
