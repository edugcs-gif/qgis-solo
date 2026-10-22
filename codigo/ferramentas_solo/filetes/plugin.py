# -*- coding: utf-8 -*-
"""
Remover Filetes - plugin QGIS
Remove filetes (slivers) finos que sobram de operações de diferença/recorte.

Método: abertura morfológica com junção em esquadro
    aberto = buffer(buffer(g, -L/2), +L/2)
    resultado = g ∩ aberto
Tudo que for mais fino que L some; as bordas que ficam são exatamente as da
geometria original (a interseção recorta de volta qualquer excesso do buffer).
Depois limpa os vértices novos e colineares criados no ponto em que o filete
estava colado, preservando todos os vértices da geometria original.
"""
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QKeySequence
from qgis.PyQt.QtWidgets import (
    QDialog, QFormLayout, QDoubleSpinBox, QCheckBox,
    QDialogButtonBox, QLabel, QMessageBox,
)
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6
from qgis.core import (
    Qgis, QgsGeometry, QgsPointXY, QgsFeature, QgsVectorLayer,
    QgsWkbTypes, QgsMessageLog,
)

TITULO = "Remover Filetes"


def _enum(base, *caminhos):
    """Primeiro enum existente (compatibilidade QGIS 3 / QGIS 4)."""
    for c in caminhos:
        v = base
        try:
            for n in c.split("."):
                v = getattr(v, n)
            return v
        except AttributeError:
            continue
    raise AttributeError(caminhos)


N_INFO = _enum(Qgis, "MessageLevel.Info", "Info")
N_AVISO = _enum(Qgis, "MessageLevel.Warning", "Warning")
N_CRITICO = _enum(Qgis, "MessageLevel.Critical", "Critical")
N_SUCESSO = _enum(Qgis, "MessageLevel.Success", "Success")
POLIGONO = _enum(Qgis, "GeometryType.Polygon") if hasattr(Qgis, "GeometryType") else QgsWkbTypes.PolygonGeometry
BTN_OK = _enum(QDialogButtonBox, "StandardButton.Ok", "Ok")
BTN_CANCELAR = _enum(QDialogButtonBox, "StandardButton.Cancel", "Cancel")
ACEITO = _enum(QDialog, "DialogCode.Accepted", "Accepted")
TOL_COLINEAR = 0.001      # 1 mm: vértice novo a menos disso da reta vizinha é removido
TOL_DUP = 0.001           # 1 mm: vértices consecutivos mais próximos que isso são duplicados
CASAS_VERTICE = 4         # arredondamento p/ reconhecer vértice original (0,1 mm)
MITER_LIMIT = 5.0


# ----------------------------------------------------------------- geometria
def _buffer_esquadro(g, dist):
    """Buffer com ponta reta e junção em esquadro (mantém cantos vivos)."""
    try:
        return g.buffer(dist, 1, Qgis.EndCapStyle.Flat, Qgis.JoinStyle.Miter, MITER_LIMIT)
    except (AttributeError, TypeError):
        return g.buffer(dist, 1, QgsGeometry.CapFlat, QgsGeometry.JoinStyleMiter, MITER_LIMIT)


def _partes_poligono(g):
    """Lista de QgsGeometry (polígono simples) contidas em g."""
    if g is None or g.isEmpty():
        return []
    partes = []
    for p in g.asGeometryCollection():
        if p.type() != POLIGONO or p.isEmpty():
            continue
        if p.isMultipart():
            partes.extend(QgsGeometry.fromPolygonXY(pp) for pp in p.asMultiPolygon())
        else:
            partes.append(p)
    return partes


def _chave(pt):
    return (round(pt.x(), CASAS_VERTICE), round(pt.y(), CASAS_VERTICE))


def _dist_ponto_segmento(p, a, b):
    ax, ay, bx, by, px, py = a.x(), a.y(), b.x(), b.y(), p.x(), p.y()
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _dedup(pts, exatos):
    """Remove vértices consecutivos a menos de TOL_DUP um do outro (anel aberto).
    Do par, fica o que é vértice original exato; se nenhum for, fica o primeiro."""
    mudou = True
    while mudou and len(pts) > 3:
        mudou = False
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if a.sqrDist(b) <= TOL_DUP * TOL_DUP:
                a_orig = (a.x(), a.y()) in exatos
                b_orig = (b.x(), b.y()) in exatos
                del pts[(i + 1) % n if (a_orig or not b_orig) else i]
                mudou = True
                break
    return pts


def _limpar_anel(anel, originais, exatos):
    """Remove duplicados e vértices que NÃO existiam no original e ficaram colineares."""
    pts = list(anel[:-1]) if len(anel) > 1 and anel[0] == anel[-1] else list(anel)
    pts = _dedup(pts, exatos)
    mudou = True
    while mudou and len(pts) > 3:
        mudou = False
        n = len(pts)
        for i in range(n):
            p = pts[i]
            if _chave(p) in originais:
                continue
            a, b = pts[i - 1], pts[(i + 1) % n]
            if _dist_ponto_segmento(p, a, b) <= TOL_COLINEAR:
                del pts[i]
                mudou = True
                break
    pts.append(QgsPointXY(pts[0]))
    return pts


def _limpar_vertices(parte, originais, exatos):
    rings = parte.asPolygon()
    if not rings:
        return parte
    novos = [_limpar_anel(r, originais, exatos) for r in rings]
    novos = [r for r in novos if len(r) >= 4]
    if not novos:
        return parte
    g = QgsGeometry.fromPolygonXY(novos)
    return g if g.isGeosValid() else parte


def remover_filetes(geom, largura, area_min):
    """Retorna lista de partes (QgsGeometry polígono) sem filetes."""
    g = QgsGeometry(geom)
    if not g.isGeosValid():
        g = g.makeValid()
    d = largura / 2.0
    aberto = _buffer_esquadro(_buffer_esquadro(g, -d), d)
    if aberto is None or aberto.isEmpty():
        return []
    res = g.intersection(aberto)
    verts = list(_vertices(g))
    originais = {_chave(v) for v in verts}
    exatos = {(v.x(), v.y()) for v in verts}
    partes = []
    for p in _partes_poligono(res):
        if p.area() < area_min:
            continue
        partes.append(_limpar_vertices(p, originais, exatos))
    partes.sort(key=lambda x: x.area(), reverse=True)
    return partes


def _vertices(g):
    for v in g.vertices():
        yield QgsPointXY(v.x(), v.y())


# ----------------------------------------------------------------- diálogo
class DialogoFiletes(QDialog):
    def __init__(self, parent, n_sel, nome_camada):
        super().__init__(parent)
        self.setWindowTitle(TITULO)
        lay = QFormLayout(self)
        lay.addRow(QLabel(f"Camada: <b>{nome_camada}</b>"))

        self.sp_largura = QDoubleSpinBox()
        self.sp_largura.setDecimals(3)
        self.sp_largura.setRange(0.001, 10.0)
        self.sp_largura.setSingleStep(0.01)
        self.sp_largura.setValue(0.10)
        self.sp_largura.setSuffix(" m")
        self.sp_largura.setToolTip("Tudo que for mais fino que isso é removido.\n"
                                   "Use um valor menor que a feição mais fina que deve ficar.")
        lay.addRow("Largura máxima do filete:", self.sp_largura)

        self.sp_area = QDoubleSpinBox()
        self.sp_area.setDecimals(4)
        self.sp_area.setRange(0.0, 100.0)
        self.sp_area.setSingleStep(0.01)
        self.sp_area.setValue(0.01)
        self.sp_area.setSuffix(" m²")
        lay.addRow("Descartar pedaço menor que:", self.sp_area)

        self.ck_sel = QCheckBox(f"Somente feições selecionadas ({n_sel})")
        self.ck_sel.setChecked(n_sel > 0)
        self.ck_sel.setEnabled(n_sel > 0)
        lay.addRow(self.ck_sel)

        self.ck_excluir = QCheckBox("Excluir feições que forem só filete")
        self.ck_excluir.setChecked(True)
        lay.addRow(self.ck_excluir)

        self.ck_explodir = QCheckBox("Camada simples: partes extras viram feições novas")
        self.ck_explodir.setChecked(True)
        lay.addRow(self.ck_explodir)

        bb = QDialogButtonBox(BTN_OK | BTN_CANCELAR)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)


# ----------------------------------------------------------------- plugin
class RemoverFiletes:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar
        self.menu = menu or TITULO
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, TITULO, self.iface.mainWindow())
        self.action.setObjectName("mFerramentasSoloRemoverFiletes")
        self.action.setShortcut(QKeySequence("Ctrl+Alt+F"))   # padrão; pode ser trocado em Atalhos de teclado
        self.action.setToolTip(f"{TITULO} (Ctrl+Alt+F)")
        self.action.triggered.connect(self.run)
        if self.toolbar is not None:
            self.toolbar.addAction(self.action)
        else:
            self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu(self.menu, self.action)

    def unload(self):
        if self.action:
            if self.toolbar is not None:
                try:
                    self.toolbar.removeAction(self.action)
                except RuntimeError:
                    pass
            else:
                self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginVectorMenu(self.menu, self.action)
            self.action.deleteLater()
            self.action = None

    def _msg(self, texto, nivel=N_INFO, dur=6):
        self.iface.messageBar().pushMessage(TITULO, texto, level=nivel, duration=dur)

    def run(self):
        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer) or \
                layer.geometryType() != POLIGONO:
            self._msg("Selecione uma camada de polígonos no painel de camadas.", N_AVISO)
            return

        dlg = DialogoFiletes(self.iface.mainWindow(), layer.selectedFeatureCount(), layer.name())
        if (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()) != ACEITO:
            return
        largura = dlg.sp_largura.value()
        area_min = dlg.sp_area.value()
        so_sel = dlg.ck_sel.isChecked()
        excluir = dlg.ck_excluir.isChecked()
        explodir = dlg.ck_explodir.isChecked()

        feats = list(layer.getSelectedFeatures() if so_sel else layer.getFeatures())
        if not feats:
            self._msg("Nenhuma feição para processar.", N_AVISO)
            return

        if not layer.isEditable() and not layer.startEditing():
            self._msg("Não foi possível entrar em edição nesta camada.", N_CRITICO)
            return

        multi = QgsWkbTypes.isMultiType(layer.wkbType())
        tem_z = QgsWkbTypes.hasZ(layer.wkbType())
        tem_m = QgsWkbTypes.hasM(layer.wkbType())
        pks = set(layer.dataProvider().pkAttributeIndexes())

        def ajustar(g):
            if multi:
                g.convertToMultiType()
            else:
                g.convertToSingleType()
            if tem_z:
                g.get().addZValue(0)
            if tem_m:
                g.get().addMValue(0)
            return g

        alteradas = excluidas = novas = 0
        layer.beginEditCommand(TITULO)
        try:
            for f in feats:
                g = f.geometry()
                if g is None or g.isEmpty():
                    continue
                partes = remover_filetes(g, largura, area_min)

                if not partes:
                    if excluir:
                        layer.deleteFeature(f.id())
                        excluidas += 1
                    continue

                area_ant = g.area()
                area_nova = sum(p.area() for p in partes)
                n_vert_ant = sum(1 for _ in g.vertices())
                n_vert_novo = sum(sum(1 for _ in p.vertices()) for p in partes)
                if abs(area_ant - area_nova) < 1e-9 and n_vert_ant == n_vert_novo:
                    continue  # nada mudou

                if multi:
                    nova = QgsGeometry.collectGeometry(partes)
                    layer.changeGeometry(f.id(), ajustar(nova))
                else:
                    layer.changeGeometry(f.id(), ajustar(QgsGeometry(partes[0])))
                    if explodir:
                        for p in partes[1:]:
                            nf = QgsFeature(layer.fields())
                            attrs = f.attributes()
                            for i in pks:
                                attrs[i] = None
                            nf.setAttributes(attrs)
                            nf.setGeometry(ajustar(QgsGeometry(p)))
                            layer.addFeature(nf)
                            novas += 1
                alteradas += 1
            layer.endEditCommand()
        except Exception as e:
            layer.destroyEditCommand()
            QgsMessageLog.logMessage(repr(e), TITULO, N_CRITICO)
            QMessageBox.critical(self.iface.mainWindow(), TITULO, f"Erro: {e}")
            return

        layer.triggerRepaint()
        txt = f"{alteradas} feição(ões) corrigida(s)"
        if excluidas:
            txt += f", {excluidas} excluída(s) (só filete)"
        if novas:
            txt += f", {novas} parte(s) nova(s)"
        txt += ". Edições pendentes — revise e salve (Ctrl+Z desfaz)."
        self._msg(txt, N_SUCESSO if (alteradas or excluidas) else N_INFO, 10)
