# -*- coding: utf-8 -*-
"""Medidas ao vivo nas ferramentas de desenho e de mover vértice.

Autor: Edu Gabriel Castilho Sembalista. Estilo e regras de medição herdados do
"Comprimento e Área" de Joel Victor Feitosa da Silva (ver comprimento.py).

Enquanto se desenha (ou se arrasta um vértice) mostra no mapa:
  - o comprimento de cada lado da feição em construção/edição;
  - em destaque (magenta) os lados que mudam junto com o cursor;
  - a área do polígono (azul, no centroide);
  - o comprimento total, para linhas.

Usa as mesmas opções do botão "Comprimento e Área" (casas decimais, vírgula,
tamanho da fonte e elipsoidal). Liga/desliga pelo botão "Medidas ao desenhar"
da barra ou com a tecla M dentro das ferramentas; a escolha fica salva.
"""
import math
import os

from qgis.PyQt.QtCore import QPointF, QRectF
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetricsF, QIcon, QPainterPath, QPen

try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

from qgis.core import (
    QgsCoordinateTransform, QgsDistanceArea, QgsGeometry, QgsMessageLog, QgsPointXY,
    QgsProject, QgsSettings, QgsUnitTypes, QgsWkbTypes,
)
from qgis.gui import QgsMapCanvasItem

from ..guias.compat import AA, NIVEL_AVISO, POLIGONO, TEXT_AA, QtC
from .comprimento import _meters, _sq_meters, formatar

CHAVE = "FerramentasSolo/medidas_ao_vivo"
ICONE = os.path.join(os.path.dirname(__file__), "icon_ao_vivo.svg")
TITULO = "Medidas ao desenhar (comprimento e área)"
MAX_ROTULOS = 5000

COR_LADO = QColor(20, 20, 20)        # mesmo preto dos rótulos fixos
COR_VIVO = QColor(200, 0, 170)       # lados que acompanham o cursor
COR_AREA = QColor(0, 102, 204)       # mesmo azul da área dos rótulos fixos


class Medidor:
    """Mede no SRC da camada, com as mesmas regras do Comprimento e Área."""

    def __init__(self, crs, elipsoidal):
        projeto = QgsProject.instance()
        self.elips = elipsoidal or crs.isGeographic()
        self.da = QgsDistanceArea()
        self.da.setSourceCrs(crs, projeto.transformContext())
        self.da.setEllipsoid(projeto.ellipsoid())
        if self.elips:
            self.f_comp = QgsUnitTypes.fromUnitToUnitFactor(self.da.lengthUnits(), _meters())
            self.f_area = QgsUnitTypes.fromUnitToUnitFactor(self.da.areaUnits(), _sq_meters())
        else:
            self.f_comp = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), _meters())
            self.f_area = self.f_comp * self.f_comp

    def comprimento(self, a, b):
        if self.elips:
            return self.da.measureLine(QgsPointXY(a), QgsPointXY(b)) * self.f_comp
        return math.hypot(b.x() - a.x(), b.y() - a.y()) * self.f_comp

    def area(self, geom):
        if self.elips:
            return self.da.measureArea(geom) * self.f_area
        return geom.area() * self.f_area


class _ItemMedidas(QgsMapCanvasItem):
    """Camada de desenho por cima do mapa (como os rubber bands)."""

    def __init__(self, canvas, dono):
        super().__init__(canvas)
        self._canvas = canvas
        self.dono = dono
        self._tam = None
        self.setZValue(1000)

    def atualizar(self):
        try:
            tam = (self._canvas.width(), self._canvas.height())
            if tam != self._tam:
                self.prepareGeometryChange()
                self._tam = tam
            self.update()
        except RuntimeError:   # QGIS fechando: canvas já destruído
            pass

    def boundingRect(self):
        w, h = self._tam or (self._canvas.width(), self._canvas.height())
        o = self.pos()
        return QRectF(-o.x() - 20, -o.y() - 20, w + 40, h + 40)

    def _px(self, p):
        q = self.toCanvasCoordinates(p)
        o = self.pos()
        return QPointF(q.x() - o.x(), q.y() - o.y())

    def paint(self, painter, option=None, widget=None):
        d = self.dono.dados
        if not d:
            return
        try:
            self._pintar(painter, d)
        except Exception as exc:  # nunca deixa um erro de desenho travar a ferramenta
            QgsMessageLog.logMessage("Erro nas medidas ao vivo: {}".format(exc),
                                     "Ferramentas Solo", NIVEL_AVISO)

    def _pintar(self, painter, d):
        op = self.dono.opcoes
        fonte = QFont()
        fonte.setPointSizeF(op.font_size)
        fonte.setBold(True)
        fonte_viva = QFont(fonte)
        fonte_viva.setPointSizeF(op.font_size + 1)
        fm, fmv = QFontMetricsF(fonte), QFontMetricsF(fonte_viva)

        halo = QPen(QColor(255, 255, 255, 235))
        halo.setWidthF(3.0)
        halo.setJoinStyle(QtC.RoundJoin)
        pinc = {False: QBrush(COR_LADO), True: QBrush(COR_VIVO)}

        painter.save()
        painter.setRenderHint(AA, True)
        painter.setRenderHint(TEXT_AA, True)

        def texto(x, y, ang, s, f, m, pincel, centralizado=True):
            tw = m.horizontalAdvance(s)
            painter.save()
            painter.translate(x, y)
            if ang:
                painter.rotate(ang)
            path = QPainterPath()
            path.addText(-tw / 2.0 if centralizado else 0.0, (m.ascent() - m.descent()) / 2.0, f, s)
            painter.strokePath(path, halo)
            painter.fillPath(path, pincel)
            painter.restore()

        n = 0
        try:
            for pts, comps, vivos, fechado, buraco in d["aneis"]:
                pix = [self._px(p) for p in pts]
                sgn = 1.0
                if fechado:
                    a2 = 0.0
                    for i in range(len(pix) - 1):
                        a2 += pix[i].x() * pix[i + 1].y() - pix[i + 1].x() * pix[i].y()
                    sgn = 1.0 if a2 > 0 else -1.0
                    if buraco:
                        sgn = -sgn
                for i, comp in enumerate(comps):
                    if comp <= 0:
                        continue
                    vivo = i in vivos
                    f, m = (fonte_viva, fmv) if vivo else (fonte, fm)
                    s = formatar(comp, op.decimals, op.comma)
                    p1, p2 = pix[i], pix[i + 1]
                    dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
                    plen = math.hypot(dx, dy)
                    if plen < 1.0:
                        continue
                    if not vivo and plen < m.horizontalAdvance(s) + 8:
                        continue   # lado fixo curto demais na tela (igual aos rótulos fixos)
                    ux, uy = dx / plen, dy / plen
                    if fechado:
                        ox, oy = uy * sgn, -ux * sgn
                    else:
                        ox, oy = uy, -ux
                        if oy > 0:
                            ox, oy = -ox, -oy
                    off = m.height() / 2.0 + 3.0
                    ang = math.degrees(math.atan2(dy, dx))
                    if ang > 90:
                        ang -= 180
                    elif ang < -90:
                        ang += 180
                    texto((p1.x() + p2.x()) / 2.0 + ox * off, (p1.y() + p2.y()) / 2.0 + oy * off,
                          ang, s, f, m, pinc[vivo])
                    n += 1
                    if n >= MAX_ROTULOS:
                        return

            if d.get("area") is not None:
                c = self._px(d["area_pt"])
                texto(c.x(), c.y(), 0, formatar(d["area"], op.decimals, op.comma) + " m²",
                      fonte_viva, fmv, QBrush(COR_AREA))

            if d.get("total") is not None:
                c = self._px(d["total_pt"])
                texto(c.x() + 16, c.y() - 14, 0,
                      "Total " + formatar(d["total"], op.decimals, op.comma) + " m",
                      fonte_viva, fmv, pinc[True], centralizado=False)
        finally:
            painter.restore()


class MedidasAoVivo:
    """Liga as ferramentas de desenho/mover vértice às medidas no mapa."""

    def __init__(self, iface, opcoes):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.opcoes = opcoes            # instância do ComprimentoSegmentos (decimais, vírgula...)
        self.ligado = QgsSettings().value(CHAVE, True, type=bool)
        self.dados = None
        self.item = None
        self.acao = None

    # ---------- botão liga/desliga ----------
    def criar_acao(self, parent):
        self.acao = QAction(QIcon(ICONE), TITULO, parent)
        self.acao.setObjectName("mFerramentasSoloMedidasAoVivo")
        self.acao.setCheckable(True)
        self.acao.setChecked(self.ligado)
        self.acao.toggled.connect(self._ao_alternar)
        self._dica()
        return self.acao

    def _dica(self):
        if self.acao is not None:
            self.acao.setToolTip(
                "%s — %s\nMostra comprimento dos lados e área enquanto desenha\n"
                "ou move vértice com as ferramentas de guias.\n"
                "Tecla M dentro das ferramentas também liga/desliga."
                % (TITULO, "Ligado" if self.ligado else "Desligado"))

    def _ao_alternar(self, marcado):
        self.ligado = bool(marcado)
        QgsSettings().setValue(CHAVE, self.ligado)
        self._dica()
        if not self.ligado:
            self.limpar()

    def alternar(self):
        """Tecla M: inverte e devolve o novo estado."""
        if self.acao is not None:
            self.acao.setChecked(not self.ligado)   # dispara _ao_alternar
        else:
            self._ao_alternar(not self.ligado)
        return self.ligado

    # ---------- dados ----------
    def mostrar(self, lyr, aneis, geom_area=None, em_camada=True, total_pt=None, feicao=None):
        """aneis: lista de (pontos, buraco, vivos) — pontos no SRC da camada
        (em_camada=True) ou do mapa; anel fechado quando o último ponto = primeiro.
        geom_area: polígono para a área (mesmo SRC dos pontos).
        total_pt: ponto do mapa onde mostrar o total (só linhas).
        feicao: (camada, fid) em edição — o rótulo fixo dela é escondido."""
        if not self.ligado or lyr is None:
            self.limpar()
            return
        projeto = QgsProject.instance()
        ctx = projeto.transformContext()
        crs_mapa = self.canvas.mapSettings().destinationCrs()
        if em_camada:
            p_camada = lambda p: p                                            # noqa: E731
            ct = QgsCoordinateTransform(lyr.crs(), crs_mapa, ctx)
            p_mapa = ct.transform
        else:
            ct = QgsCoordinateTransform(crs_mapa, lyr.crs(), ctx)
            p_camada = ct.transform
            p_mapa = lambda p: p                                              # noqa: E731
        med = Medidor(lyr.crs(), self.opcoes.ellipsoidal)

        saida, total = [], 0.0
        for pts, buraco, vivos in aneis:
            if len(pts) < 2:
                continue
            pc = [QgsPointXY(p_camada(QgsPointXY(p))) for p in pts]
            pm = [QgsPointXY(p_mapa(QgsPointXY(p))) for p in pts]
            comps = [med.comprimento(pc[i], pc[i + 1]) for i in range(len(pc) - 1)]
            total += sum(comps)
            fechado = len(pts) >= 4 and _mesmo(pts[0], pts[-1])
            saida.append((pm, comps, set(vivos), fechado, buraco))

        d = {"aneis": saida, "area": None, "total": None}
        if geom_area is not None and not geom_area.isEmpty():
            g = QgsGeometry(geom_area)
            if not em_camada:
                g.transform(ct)
            if g.type() == POLIGONO:
                d["area"] = med.area(g)
                c = geom_area.centroid()
                if c is not None and not c.isEmpty():
                    d["area_pt"] = QgsPointXY(p_mapa(c.asPoint()))
                else:
                    d["area"] = None
        if total_pt is not None and total > 0:
            d["total"] = total
            d["total_pt"] = QgsPointXY(total_pt)

        self.dados = d
        if self.opcoes is not None and hasattr(self.opcoes, "definir_excluida"):
            self.opcoes.definir_excluida((feicao[0].id(), feicao[1]) if feicao else None)
        if self.item is None:
            self.item = _ItemMedidas(self.canvas, self)
        self.item.atualizar()

    def limpar(self):
        self.dados = None
        if self.item is not None:
            self.item.atualizar()
        if self.opcoes is not None and getattr(self.opcoes, "excluir", None):
            self.opcoes.definir_excluida(None)

    def remover(self):
        self.limpar()
        if self.item is not None:
            try:
                self.canvas.scene().removeItem(self.item)
            except Exception:
                pass
            self.item = None
        if self.acao is not None:
            self.acao.deleteLater()
            self.acao = None


def _mesmo(a, b):
    return abs(a.x() - b.x()) < 1e-9 and abs(a.y() - b.y()) < 1e-9


def aneis_da_geometria(geom, poligono):
    """Anéis/linhas de uma geometria como listas de QgsPointXY -> [(pontos, buraco)]."""
    g = geom
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g = QgsGeometry(g)
        g.convertToStraightSegment()
    multi = QgsWkbTypes.isMultiType(g.wkbType())
    out = []
    if poligono:
        for pol in (g.asMultiPolygon() if multi else [g.asPolygon()]):
            for i, anel in enumerate(pol):
                out.append((anel, i > 0))
    else:
        for linha in (g.asMultiPolyline() if multi else [g.asPolyline()]):
            out.append((linha, False))
    return out
