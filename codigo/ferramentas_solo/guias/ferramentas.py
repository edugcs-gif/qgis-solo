# -*- coding: utf-8 -*-
"""Ferramentas de mapa: Mover vértice com guias (extend) e Desenhar com guias 90°/45°."""
import math

from qgis.PyQt.QtCore import Qt, QPoint
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QApplication, QToolTip
from qgis.core import (
    Qgis, QgsCoordinateTransform, QgsEditFormConfig, QgsFeatureRequest, QgsProject, QgsGeometry, QgsPoint, QgsPointXY,
    QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsRectangle, QgsSettings,
    QgsUnitTypes, QgsVectorLayer, QgsVectorLayerUtils, QgsWkbTypes,
)
from qgis.gui import QgsAttributeEditorContext, QgsMapTool, QgsRubberBand, QgsVertexMarker

from .compat import (FORM_PADRAO, FORM_SUPRIMIR, LINHA, MODO_ADICIONAR, NIVEL_AVISO,
                     NIVEL_INFO, POLIGONO, PONTO, RB_CIRCULO, VM, QtC, pos_evento)
from .guias import (dist_ponto_segmento, guias_angulares, intersecao, intersecao_segmento,
                    intersecao_segmentos, nova_guia)
from ..medidas.ao_vivo import aneis_da_geometria

TOL_PX = 12            # tolerância de captura das guias (pixels)
MOSTRAR_FRACAS_PX = 70  # guias secundárias só aparecem com o cursor a menos disso (pixels)



def _dist(a, b):
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _mesmo_pt(a, b):
    return abs(a.x() - b.x()) < 1e-9 and abs(a.y() - b.y()) < 1e-9


class _BaseGuias(QgsMapTool):
    """Base comum: rubber bands, resolução do snap nas guias, teclas."""

    def __init__(self, iface, acao, medidas=None, intervalo=None, meio=None):
        self.iface = iface
        canvas = iface.mapCanvas()
        super().__init__(canvas)
        self.setAction(acao)
        self.setCursor(QtC.CrossCursor)
        self.medidas = medidas      # MedidasAoVivo (comprimento/área enquanto desenha)
        self.intervalo = intervalo  # IntervaloDistancia (aderência por passo de distância)
        self.meio = meio            # AderenciaMeio (ponto médio dos lados)
        self._ult_pt = None         # último ponto resolvido do cursor
        self.guias_ligadas = True
        self.mostrar_45 = True
        self.intersec_camadas = True
        self._tip = None

        self.rb_forte = self._rb(LINHA, QColor(255, 130, 0, 220), 1, QtC.DashLine)
        self.rb_fraca = self._rb(LINHA, QColor(110, 110, 110, 170), 1, QtC.DotLine)
        self.rb_ativa = self._rb(LINHA, QColor(230, 0, 200, 240), 2, QtC.SolidLine)
        self.rb_prev = self._rb(LINHA, QColor(0, 120, 255, 230), 2, QtC.SolidLine)

        self.marcador = QgsVertexMarker(canvas)
        self.marcador.setColor(QColor(230, 0, 200))
        self.marcador.setPenWidth(2)
        self.marcador.setIconSize(14)
        self.marcador.hide()

    # ---------- utilidades visuais ----------
    def _rb(self, tipo, cor, largura, estilo):
        rb = QgsRubberBand(self.canvas(), tipo)
        rb.setColor(cor)
        rb.setWidth(largura)
        rb.setLineStyle(estilo)
        return rb

    def _itens(self):
        return [self.rb_forte, self.rb_fraca, self.rb_ativa, self.rb_prev, self.marcador]

    def limpar_visual(self):
        for rb in (self.rb_forte, self.rb_fraca, self.rb_ativa):
            rb.reset(LINHA)
        self.rb_prev.reset(LINHA)
        self.marcador.hide()
        self._mostrar_tip(None, None)
        if self.medidas is not None:
            self.medidas.limpar()

    def remover_itens(self):
        cena = self.canvas().scene()
        for it in self._itens():
            try:
                cena.removeItem(it)
            except Exception:
                pass

    def _tol(self):
        return TOL_PX * self.canvas().mapUnitsPerPixel()

    def _mostrar_tip(self, texto, pos):
        if not texto:
            if self._tip:
                QToolTip.hideText()
            self._tip = None
            return
        if texto != self._tip:
            QToolTip.showText(self.canvas().mapToGlobal(pos + QPoint(18, 18)), texto, self.canvas())
            self._tip = texto

    def _msg(self, texto, nivel=NIVEL_INFO, dur=4):
        self.iface.messageBar().pushMessage("Guias CAD", texto, level=nivel, duration=dur)

    def _status(self, texto):
        self.iface.statusBarIface().showMessage(texto)

    def desenhar_guias(self, guias, ativas, cursor):
        ext = self.canvas().extent()
        comp = max(ext.width(), ext.height()) * 3
        lim_fraca = MOSTRAR_FRACAS_PX * self.canvas().mapUnitsPerPixel()
        for rb in (self.rb_forte, self.rb_fraca, self.rb_ativa):
            rb.reset(LINHA)
        cont = {id(self.rb_forte): 0, id(self.rb_fraca): 0, id(self.rb_ativa): 0}
        for g in guias:
            if g in ativas:
                rb = self.rb_ativa
            elif g.forte:
                rb = self.rb_forte
            elif cursor is not None and g.distancia(cursor) < lim_fraca:
                rb = self.rb_fraca
            else:
                continue
            a, b = g.extremos(comp)
            i = cont[id(rb)]
            rb.addPoint(a, False, i)
            rb.addPoint(b, True, i)
            cont[id(rb)] = i + 1

    def filtrar(self, guias):
        """Remove guias desligadas e duplicadas (mesma linha)."""
        if not self.guias_ligadas:
            return []
        tol = self._tol() * 0.01
        out = []
        for g in guias:
            if g is None or (g.g45 and not self.mostrar_45):
                continue
            if any(g.mesma_linha(h, tol) for h in out):
                continue
            out.append(g)
        return out

    # ---------- bordas das camadas visíveis perto do cursor ----------
    def _meio_ligado(self):
        return self.meio is not None and self.meio.ligado

    def segmentos_proximos(self, p, tol, ignorar_seg=None, sempre=False):
        """Segmentos (a, b, nome_camada, ponto_médio) de todas as camadas de linha/polígono
        visíveis que passam a menos de `tol` do cursor (coordenadas do mapa). O ponto médio é
        calculado no SRC da camada (exato mesmo com reprojeção)."""
        if not self.intersec_camadas and not sempre:
            return []
        canvas = self.canvas()
        crs_mapa = canvas.mapSettings().destinationCrs()
        ret = QgsRectangle(p.x() - tol, p.y() - tol, p.x() + tol, p.y() + tol)
        out = []
        for lyr in canvas.layers():
            if not isinstance(lyr, QgsVectorLayer) or lyr.geometryType() not in (LINHA, POLIGONO):
                continue
            ct = QgsCoordinateTransform(lyr.crs(), crs_mapa, QgsProject.instance())
            try:
                ret_lyr = self.toLayerCoordinates(lyr, ret)
            except Exception:
                continue
            req = QgsFeatureRequest().setFilterRect(ret_lyr).setNoAttributes()
            req.setLimit(200)
            for f in lyr.getFeatures(req):
                g = f.geometry()
                if g is None or g.isEmpty():
                    continue
                for linha in self._linhas(g):
                    for i in range(len(linha) - 1):
                        a, b = linha[i], linha[i + 1]
                        # pré-filtro barato no CRS da camada
                        if (max(a.x(), b.x()) < ret_lyr.xMinimum() or min(a.x(), b.x()) > ret_lyr.xMaximum() or
                                max(a.y(), b.y()) < ret_lyr.yMinimum() or min(a.y(), b.y()) > ret_lyr.yMaximum()):
                            continue
                        am, bm = ct.transform(a), ct.transform(b)
                        if dist_ponto_segmento(p, am, bm) > tol:
                            continue
                        if ignorar_seg and ignorar_seg(lyr, f.id(), am, bm):
                            continue
                        meio = ct.transform(QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
                        out.append((am, bm, lyr.name(), meio))
        return out

    @staticmethod
    def _linhas(g):
        if QgsWkbTypes.isCurvedType(g.wkbType()):
            g = QgsGeometry(g.constGet().segmentize())
        if g.type() == POLIGONO:
            g = QgsGeometry(g.constGet().boundary())
        if g.isMultipart():
            return g.asMultiPolyline()
        return [g.asPolyline()]

    # ---------- núcleo: onde o ponto "gruda" ----------
    def resolver(self, p, guias, pontos_extra=(), ignorar_match=None, ignorar_seg=None):
        """Retorna (ponto, rótulo, guias_ativas).

        Prioridade: vértice do snap do QGIS > meio do lado, interseções (guia x guia, guia x
        borda de qualquer camada visível, borda x borda entre camadas) e pontos extras, o mais
        perto do cursor > projeção numa guia > snap em segmento do QGIS > livre.
        Segure SHIFT para desenhar livre.
        """
        if QApplication.keyboardModifiers() & QtC.ShiftModifier:
            return p, "", []
        tol = self._tol()

        m = self.canvas().snappingUtils().snapToMap(p)
        if m.isValid() and ignorar_match and ignorar_match(m):
            m = None
        if m is not None and m.isValid() and m.hasVertex():
            return QgsPointXY(m.point()), "Vértice", []

        melhor = [None]

        def considerar(pt, rotulo, gs):
            if pt is None:
                return
            d = _dist(pt, p)
            if d <= tol and (melhor[0] is None or d < melhor[0][0] - 1e-9 * max(1.0, tol)):
                melhor[0] = (d, pt, rotulo, gs)

        # pontos extras e meio do lado primeiro: num empate (mesmo ponto) fica o nome deles
        for pt, rot in pontos_extra:
            considerar(pt, rot, [])
        meio = self._meio_ligado()
        proximos = self.segmentos_proximos(p, tol, ignorar_seg, sempre=meio)
        # meio do lado (ponto médio de cada face das camadas visíveis)
        if meio:
            for _a, _b, nome, pm in proximos:
                considerar(pm, "Meio do lado (%s)" % nome, [])
        # guia x guia
        for i in range(len(guias)):
            for j in range(i + 1, len(guias)):
                g1, g2 = guias[i], guias[j]
                considerar(intersecao(g1, g2), "%s × %s" % (g1.rotulo, g2.rotulo), [g1, g2])
        segs = [(a, b, nome) for a, b, nome, _pm in proximos] if self.intersec_camadas else []
        if m is not None and m.isValid() and m.hasEdge():
            a, b = m.edgePoints()
            segs.append((QgsPointXY(a), QgsPointXY(b), m.layer().name() if m.layer() else "borda"))
        # guia x borda (extend até a borda de qualquer camada)
        for g in guias:
            for a, b, nome in segs:
                considerar(intersecao_segmento(g, a, b), "%s × %s" % (g.rotulo, nome), [g])
        # borda x borda (interseção entre camadas / feições)
        for i in range(len(segs)):
            a, b, n1 = segs[i]
            for j in range(i + 1, len(segs)):
                c, d, n2 = segs[j]
                x = intersecao_segmentos(a, b, c, d)
                if x is None:
                    continue
                # cruzamento no próprio vértice comum de dois lados vizinhos não conta
                if min(_dist(x, a), _dist(x, b)) < 1e-9 and min(_dist(x, c), _dist(x, d)) < 1e-9:
                    continue
                considerar(x, "Interseção %s × %s" % (n1, n2), [])
        if melhor[0]:
            _, pt, rot, gs = melhor[0]
            return pt, rot, gs

        proj = None
        for g in guias:
            q, d = g.projetar(p)
            if d <= tol and (proj is None or d < proj[0]):
                proj = (d, q, g)
        if proj:
            return proj[1], proj[2].rotulo, [proj[2]]

        if m is not None and m.isValid():
            return QgsPointXY(m.point()), "Borda", []
        return p, "", []

    def mostrar_ponto(self, pt, rotulo, pos):
        if rotulo:
            if rotulo == "Vértice":
                self.marcador.setIconType(VM.ICON_BOX)
            elif rotulo.startswith("Meio"):
                self.marcador.setIconType(VM.ICON_TRIANGLE)
            elif "×" in rotulo or rotulo.startswith("Ponto"):
                self.marcador.setIconType(VM.ICON_X)
            else:
                self.marcador.setIconType(VM.ICON_CIRCLE)
            self.marcador.setCenter(pt)
            self.marcador.show()
        else:
            self.marcador.hide()
        self._mostrar_tip(rotulo, pos)

    # ---------- camada ----------
    TIPOS = (LINHA, POLIGONO)               # tipos de camada que a ferramenta edita
    NOME_TIPOS = "linha ou polígono"

    def camada_editavel(self, avisar=True):
        lyr = self.iface.activeLayer()
        if not isinstance(lyr, QgsVectorLayer) or lyr.geometryType() not in self.TIPOS:
            if avisar:
                self._msg("Selecione uma camada de %s no painel de camadas." % self.NOME_TIPOS, NIVEL_AVISO)
            return None
        if not lyr.isEditable():
            if avisar:
                self._msg("Ative a edição da camada '%s' (lápis) antes de usar a ferramenta." % lyr.name(),
                          NIVEL_AVISO)
            return None
        return lyr

    # ---------- teclas comuns ----------
    def tecla_comum(self, e):
        k = e.key()
        if k == QtC.Key_G:
            self.guias_ligadas = not self.guias_ligadas
            self._status("Guias %s" % ("LIGADAS" if self.guias_ligadas else "DESLIGADAS"))
            return True
        if k == QtC.Key_4:
            self.mostrar_45 = not self.mostrar_45
            self._status("Guias de 45° %s" % ("LIGADAS" if self.mostrar_45 else "DESLIGADAS"))
            return True
        if k == QtC.Key_I:
            self.intersec_camadas = not self.intersec_camadas
            self._status("Interseção com camadas %s" % ("LIGADA" if self.intersec_camadas else "DESLIGADA"))
            return True
        if k == QtC.Key_M and self.medidas is not None:
            ligado = self.medidas.alternar()
            self._status("Medidas ao vivo (comprimento e área) %s" % ("LIGADAS" if ligado else "DESLIGADAS"))
            return True
        if k == QtC.Key_P and self.meio is not None:
            ligado = self.meio.alternar()
            self._status("Aderência no meio do lado %s" % ("LIGADA" if ligado else "DESLIGADA"))
            return True
        if k == QtC.Key_D and self.intervalo is not None:
            ligado = self.intervalo.alternar()
            self._status("Intervalo de distância (%s m) %s" % (self.intervalo.texto(),
                                                              "LIGADO" if ligado else "DESLIGADO"))
            return True
        return False

    # ---------- intervalo de distância (aderência por passo) ----------
    def _pode_passo(self, rot, ativas):
        """O passo vale para ponto livre ou deslizando numa guia; vértice, interseções,
        borda e ponto inicial têm prioridade. SHIFT deixa tudo livre."""
        if self.intervalo is None or not self.intervalo.ligado:
            return False
        if QApplication.keyboardModifiers() & QtC.ShiftModifier:
            return False
        if not ativas:
            return rot == ""
        return len(ativas) == 1 and "×" not in rot

    def _passo(self, ancora, pt, guia, lyr):
        """Ajusta pt para que a distância até a âncora seja múltiplo do intervalo.
        Conta no SRC da camada (igual aos rótulos de comprimento). Com guia, o ponto
        continua sobre ela. Devolve o ponto no SRC do mapa, ou None."""
        if ancora is None or lyr is None or lyr.crs().isGeographic():
            return None
        try:
            metros = Qgis.DistanceUnit.Meters
        except AttributeError:
            metros = QgsUnitTypes.DistanceMeters
        f = QgsUnitTypes.fromUnitToUnitFactor(lyr.crs().mapUnits(), metros)   # unidade da camada -> m
        if not f or f <= 0:
            return None
        passo = self.intervalo.valor / f
        a = self.toLayerCoordinates(lyr, ancora)
        q = self.toLayerCoordinates(lyr, pt)
        dx, dy = q.x() - a.x(), q.y() - a.y()
        d = math.hypot(dx, dy)
        if d < 1e-12 or passo <= 0:
            return None
        n = max(1, int(round(d / passo)))
        if guia is None:
            r = n * passo
            res = QgsPointXY(a.x() + dx / d * r, a.y() + dy / d * r)
        else:
            g0 = self.toLayerCoordinates(lyr, guia.o)
            g1 = self.toLayerCoordinates(lyr, QgsPointXY(guia.o.x() + guia.dx * 100.0,
                                                          guia.o.y() + guia.dy * 100.0))
            ux, uy = g1.x() - g0.x(), g1.y() - g0.y()
            nu = math.hypot(ux, uy)
            if nu < 1e-12:
                return None
            ux, uy = ux / nu, uy / nu
            wx, wy = g0.x() - a.x(), g0.y() - a.y()
            b = wx * ux + wy * uy
            c0 = wx * wx + wy * wy
            # o círculo (âncora, n×passo) precisa alcançar a guia
            n = max(n, int(math.ceil(math.sqrt(max(0.0, c0 - b * b)) / passo - 1e-9)))
            r = n * passo
            raiz = math.sqrt(max(0.0, b * b - (c0 - r * r)))
            tq = (q.x() - g0.x()) * ux + (q.y() - g0.y()) * uy
            t = min((-b + raiz, -b - raiz), key=lambda t: abs(t - tq))
            res = QgsPointXY(g0.x() + ux * t, g0.y() + uy * t)
        return self.toMapCoordinates(lyr, res)

    def _rotulo_passo(self, rot):
        txt = "passo %s m" % self.intervalo.texto()
        return "%s · %s" % (rot, txt) if rot else txt[0].upper() + txt[1:]

    def deactivate(self):
        self.cancelar()
        super().deactivate()

    def cancelar(self):
        self.limpar_visual()

    def isEditTool(self):
        return True


# =====================================================================
class FerramentaMoverVertice(_BaseGuias):
    """Clique num vértice e mova: mostra as extensões dos dois lados vizinhos
    (extend), a interseção delas e guias de 90°/45° nos vértices vizinhos.
    Em camada de pontos, move o ponto com guias 0°/90°/45° a partir da posição original."""

    TIPOS = (PONTO, LINHA, POLIGONO)
    NOME_TIPOS = "ponto, linha ou polígono"

    def __init__(self, iface, acao, medidas=None, intervalo=None, meio=None):
        super().__init__(iface, acao, medidas, intervalo, meio)
        # linha tracejada da posição original até o cursor (deslocamento de ponto)
        self.rb_desloc = self._rb(LINHA, QColor(200, 0, 170, 200), 1, QtC.DashLine)
        self.sel = None          # dict com a edição em andamento
        self.hover = None        # (camada, vértice) sob o cursor
        self.hover_seg = None    # (camada, ponto no segmento) sob o cursor -> criar vértice
        self.pos_press = None
        self.pegou_no_press = False
        self.marcas_meio = []    # triângulos no meio dos lados ORIGINAIS do vértice em edição
        # feição sob o cursor: destaque e escolha entre sobrepostas com ↑/↓
        self.rb_hover = self._rb(POLIGONO, QColor(255, 160, 0, 235), 3, QtC.SolidLine)
        self.cands = []          # feições candidatas sob o cursor, na ordem de escolha
        self.sob_cursor = set()  # todas as feições sob o cursor (inclusive só "dentro")
        self.fid_pref = None     # (camada, fid) escolhida com as setas; vale enquanto estiver sob o cursor
        self._hover_lyr = None
        self._hover_pos = None
        self._nomes = {}         # cache do nome de exibição das feições

    def activate(self):
        super().activate()
        self._status("Mover vértice/ponto com guias: clique num vértice ou ponto para mover ou num lado para CRIAR vértice (+) · "
                     "Delete apaga · "
                     "Esc/botão direito cancela · SHIFT livre · G guias · 4 45° · I interseção c/ camadas · "
                     "M medidas · D intervalo · P meio do lado · ↑↓ troca a feição sobreposta")
        self.fid_pref = None
        self._nomes = {}

    # --- feições sob o cursor ---
    def _candidatos(self, lyr, p):
        """Feições da camada ativa sob o cursor, na ordem em que são escolhidas:
        1) com vértice perto (o mais perto primeiro), 2) com lado perto (criar vértice) e
        3) em polígonos, as que contêm o cursor. No empate, a de menor área vem antes.
        Cada uma: dict(tipo="vertice"|"lado"|"dentro", fid, geom, idx, pt, no_meio, d, area)."""
        tol = self._tol()
        ret_map = QgsRectangle(p.x() - tol, p.y() - tol, p.x() + tol, p.y() + tol)
        ret_lyr = self.toLayerCoordinates(lyr, ret_map)
        lp = self.toLayerCoordinates(lyr, p)
        ponto = QgsGeometry.fromPointXY(lp)
        tipo_camada = lyr.geometryType()
        req = QgsFeatureRequest().setFilterRect(ret_lyr).setNoAttributes()
        cands = []
        for f in lyr.getFeatures(req):
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            c = dict(fid=f.id(), geom=QgsGeometry(g), idx=-1, pt=None, no_meio=False, d=0.0,
                     area=g.area() if tipo_camada == POLIGONO else g.length())
            v, idx, _a, _b, _d2 = g.closestVertex(lp)
            if idx >= 0:
                vm = self.toMapCoordinates(lyr, QgsPointXY(v))
                d = _dist(vm, p)
                if d <= tol:
                    c.update(tipo="vertice", idx=idx, pt=vm, d=d)
                    cands.append(c)
                    continue
            if tipo_camada != PONTO:
                _d2, q, depois, _lado = g.closestSegmentWithContext(lp)
                if depois >= 0:
                    qm = self.toMapCoordinates(lyr, QgsPointXY(q))
                    d = _dist(qm, p)
                    if d <= tol:
                        no_meio = False
                        if self._meio_ligado():
                            a, b = g.vertexAt(depois - 1), g.vertexAt(depois)
                            meio = self.toMapCoordinates(lyr, QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
                            if _dist(meio, p) <= tol:
                                qm, no_meio = meio, True
                        c.update(tipo="lado", idx=depois, pt=qm, no_meio=no_meio, d=d)
                        cands.append(c)
                        continue
            if tipo_camada == POLIGONO and g.contains(ponto):
                c.update(tipo="dentro")
                cands.append(c)
        ordem = {"vertice": 0, "lado": 1, "dentro": 2}
        cands.sort(key=lambda c: (ordem[c["tipo"]], round(c["d"] / tol, 6) if tol else 0, c["area"], c["fid"]))
        return cands

    def _definir_candidatos(self, lyr, p):
        """Com vértice ou lado perto do cursor, só essas feições entram na troca (são as que o
        clique edita); senão, os polígonos que contêm o cursor (só para destacar e escolher)."""
        todos = self._candidatos(lyr, p) if lyr else []
        self.sob_cursor = {(lyr.id(), c["fid"]) for c in todos} if lyr else set()
        acao = [c for c in todos if c["tipo"] != "dentro"]
        self.cands = acao or todos

    def _escolhido(self):
        """(candidato, posição) da feição escolhida: a das setas, se ela está entre as candidatas.
        A escolha é esquecida quando a feição sai de baixo do cursor."""
        lyr = self._hover_lyr
        if self.fid_pref is not None and self.fid_pref not in self.sob_cursor:
            self.fid_pref = None
        for i, c in enumerate(self.cands):
            if lyr is not None and (lyr.id(), c["fid"]) == self.fid_pref:
                return c, i
        return (self.cands[0], 0) if self.cands else (None, -1)

    def _nome_feicao(self, lyr, fid):
        """'fid 12' e, se a camada tem nome de exibição, o valor dele (ex.: 'fid 12 · Lote 05')."""
        chave = (lyr.id(), fid)
        if chave not in self._nomes:
            nome = "fid %s" % fid
            try:
                expr = lyr.displayExpression()
                if expr:
                    f = lyr.getFeature(fid)
                    ctx = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(lyr))
                    ctx.setFeature(f)
                    v = QgsExpression(expr).evaluate(ctx)
                    v = "" if v is None else str(v).strip()
                    if v and v != "NULL" and v != str(fid):
                        nome += " · " + (v if len(v) <= 40 else v[:39] + "…")
            except Exception:
                pass
            self._nomes[chave] = nome
        return self._nomes[chave]

    def _mostrar_hover(self):
        """Destaca a feição escolhida e mostra o vértice/lado em que o clique vai agir."""
        lyr = self._hover_lyr
        c, i = self._escolhido()
        self.hover = self.hover_seg = None
        if lyr is None or c is None:
            self.rb_hover.reset(POLIGONO)
            self.marcador.hide()
            self._mostrar_tip(None, None)
            return
        self.rb_hover.reset(lyr.geometryType())
        self.rb_hover.setColor(QColor(255, 160, 0, 235))       # contorno (e preenchimento)...
        if lyr.geometryType() == POLIGONO:
            self.rb_hover.setFillColor(QColor(255, 160, 0, 45))  # ...depois o preenchimento claro
        elif lyr.geometryType() == PONTO:
            self.rb_hover.setIcon(RB_CIRCULO)
            self.rb_hover.setIconSize(16)
        self.rb_hover.setToGeometry(c["geom"], lyr)
        nome = self._nome_feicao(lyr, c["fid"])
        if c["tipo"] == "vertice":
            self.hover = (lyr, (c["d"], c["fid"], c["idx"], c["geom"], c["pt"]))
            self.marcador.setIconType(VM.ICON_BOX)
            texto = ("Ponto · " if lyr.geometryType() == PONTO else "Vértice · ") + nome
        elif c["tipo"] == "lado":
            self.hover_seg = (lyr, (c["d"], c["fid"], c["idx"], c["geom"], c["pt"], c["no_meio"]))
            self.marcador.setIconType(VM.ICON_TRIANGLE if c["no_meio"] else VM.ICON_CROSS)
            texto = ("Criar vértice no meio do lado" if c["no_meio"] else "Criar vértice") + " · " + nome
        else:
            texto = nome
        if c["pt"] is not None:
            self.marcador.setCenter(c["pt"])
            self.marcador.show()
        else:
            self.marcador.hide()
        if len(self.cands) > 1:
            texto += "  (%d de %d · ↑↓ troca)" % (i + 1, len(self.cands))
        if self._hover_pos is not None:
            self._mostrar_tip(texto, self._hover_pos)

    def _trocar_feicao(self, passo):
        """Setas ↑/↓: passa para a próxima/anterior feição sobreposta sob o cursor."""
        if self.sel is not None or len(self.cands) < 2:
            return False
        _c, i = self._escolhido()
        self.fid_pref = (self._hover_lyr.id(), self.cands[(i + passo) % len(self.cands)]["fid"])
        self._tip = None                       # força a dica a atualizar no mesmo lugar
        self._mostrar_hover()
        return True

    def _inserir_na_geom(self, lyr, g, depois, pm):
        """Cópia da geometria com um vértice novo antes do índice `depois`
        (Z/M interpolados dos vizinhos)."""
        lp = self.toLayerCoordinates(lyr, pm)
        va, vb = g.vertexAt(depois - 1), g.vertexAt(depois)
        novo = QgsPoint(lp.x(), lp.y())
        if g.constGet().is3D():
            novo.addZValue((va.z() + vb.z()) / 2)
        if g.constGet().isMeasure():
            novo.addMValue((va.m() + vb.m()) / 2)
        g2 = QgsGeometry(g)
        if not g2.insertVertex(novo, depois):
            return None
        return g2

    def _itens(self):
        return super()._itens() + [self.rb_desloc, self.rb_hover] + self.marcas_meio

    def limpar_visual(self):
        super().limpar_visual()
        self.rb_desloc.reset(LINHA)
        self.rb_hover.reset(POLIGONO)
        self._limpar_marcas_meio()

    # --- meio dos lados como estavam ANTES da edição ---
    def _meios_originais(self, lyr, geom, idx, novo):
        """Ponto médio de cada lado do vértice na posição de antes da edição (no SRC do mapa).
        Os lados mudam enquanto o vértice anda; estes pontos ficam guardados, para dar para
        levar o vértice, por exemplo, ao meio do lado em que ele estava."""
        if lyr.geometryType() == PONTO:
            return []

        def meio(i, j):
            a, b = geom.vertexAt(i), geom.vertexAt(j)
            return self.toMapCoordinates(lyr, QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))

        ant, prox = geom.adjacentVertices(idx)
        if novo:
            # vértice criado agora: o lado original é o que ele dividiu (vizinho anterior -> próximo)
            return [(meio(ant, prox), "Meio do lado original")] if ant >= 0 and prox >= 0 else []
        return [(meio(idx, n), "Meio do lado original " + nome)
                for n, nome in ((ant, "A"), (prox, "B")) if n >= 0]

    def _mostrar_marcas_meio(self):
        self._limpar_marcas_meio()
        if self.sel is None or not self._meio_ligado():
            return
        for pt, _rot in self.sel.get("meios_orig", []):
            mk = QgsVertexMarker(self.canvas())
            mk.setIconType(VM.ICON_TRIANGLE)
            mk.setColor(QColor(200, 0, 170, 160))
            mk.setIconSize(9)
            mk.setPenWidth(1)
            mk.setCenter(pt)
            self.marcas_meio.append(mk)

    def _limpar_marcas_meio(self):
        cena = self.canvas().scene()
        for mk in self.marcas_meio:
            try:
                cena.removeItem(mk)
            except Exception:
                pass
        self.marcas_meio = []

    def _montar_guias(self, lyr, g, idx):
        def P(i):
            return self.toMapCoordinates(lyr, QgsPointXY(g.vertexAt(i)))

        V = P(idx)
        if lyr.geometryType() == PONTO:
            # ponto não tem lados: guias horizontais/verticais (e 45°) a partir da posição original
            return self.filtrar(guias_angulares(V, 1.0, 0.0, "(eixo)", forte_0=True, forte_90=True,
                                                rot0="0° (eixo)")), V
        ant, prox = g.adjacentVertices(idx)
        guias = []
        for lado, n, nome in ((0, ant, "A"), (1, prox, "B")):
            if n < 0:
                continue
            N = P(n)
            viz = g.adjacentVertices(n)
            nn = viz[0] if lado == 0 else viz[1]
            if nn >= 0 and nn != idx:
                O = P(nn)
                dx, dy = N.x() - O.x(), N.y() - O.y()
                # extensão do lado vizinho (o "extend") + 90°/45° em relação a ele
                guias += guias_angulares(N, dx, dy, "lado " + nome, forte_0=True,
                                         rot0="Extensão " + nome)
            # direção original da aresta vizinho -> vértice (desliza sobre a própria aresta)
            guias.append(nova_guia(N, V.x() - N.x(), V.y() - N.y(), "Direção original " + nome))
        return self.filtrar(guias), V

    # --- eventos ---
    def canvasMoveEvent(self, e):
        p = e.mapPoint()
        if self.sel is None:
            lyr = self.camada_editavel(avisar=False)
            self._hover_lyr = lyr
            self._hover_pos = pos_evento(e)
            self._definir_candidatos(lyr, p)
            self._mostrar_hover()
            return
        pt, rot, ativas = self._resolver_sel(p)
        g2 = self._previa(pt)
        self.desenhar_guias(self.sel["guias"], ativas, pt)
        self.mostrar_ponto(pt, rot, pos_evento(e))
        self._medir(g2, pt)

    def _resolver_sel(self, p):
        s = self.sel
        orig = s["orig"]

        def ignorar(m):
            # não grudar no próprio vértice que está sendo movido nem nas arestas dele
            if m.hasVertex() and _dist(QgsPointXY(m.point()), orig) < 1e-9:
                return True
            if m.hasEdge() and m.layer() == s["lyr"] and m.featureId() == s["fid"]:
                a, b = m.edgePoints()
                return _dist(QgsPointXY(a), orig) < 1e-9 or _dist(QgsPointXY(b), orig) < 1e-9
            return False

        def ignorar_seg(lyr, fid, a, b):
            # arestas do próprio vértice em movimento
            return (lyr == s["lyr"] and fid == s["fid"] and
                    (_dist(a, orig) < 1e-9 or _dist(b, orig) < 1e-9))

        extras = s.get("meios_orig", []) if self._meio_ligado() else []
        pt, rot, ativas = self.resolver(p, s["guias"], extras, ignorar_match=ignorar, ignorar_seg=ignorar_seg)
        if self._pode_passo(rot, ativas):
            # na guia: o lado daquela guia (a guia nasce no vértice vizinho); livre: o lado anterior
            ancora, guia = (ativas[0].o, ativas[0]) if ativas else (s["ancora"], None)
            ajustado = self._passo(ancora, pt, guia, s["lyr"])
            if ajustado is not None:
                pt, rot = ajustado, self._rotulo_passo(rot)
        return pt, rot, ativas

    def _previa(self, pt):
        s = self.sel
        lp = self.toLayerCoordinates(s["lyr"], pt)
        g2 = QgsGeometry(s["geom"])
        g2.moveVertex(lp.x(), lp.y(), s["idx"])
        self.rb_prev.setToGeometry(g2, s["lyr"])
        self._ult_pt = QgsPointXY(pt)
        return g2

    def _medir(self, g2, pt):
        """Comprimento dos lados da feição em edição (os dois lados do vértice
        em destaque) e área do polígono, acompanhando o cursor."""
        m = self.medidas
        if m is None or not m.ligado or self.sel is None:
            self.rb_desloc.reset(LINHA)
            return
        s = self.sel
        lyr = s["lyr"]
        lp = self.toLayerCoordinates(lyr, pt)
        if lyr.geometryType() == PONTO:
            # ponto: mostra quanto ele está sendo deslocado da posição original
            self.rb_desloc.reset(LINHA)
            if _dist(pt, s["orig"]) > 1e-12:
                self.rb_desloc.addPoint(s["orig"], False)
                self.rb_desloc.addPoint(pt, True)
                orig_l = self.toLayerCoordinates(lyr, s["orig"])
                m.mostrar(lyr, [([orig_l, lp], False, [0])], em_camada=True)
            else:
                m.limpar()
            return
        poligono = lyr.geometryType() == POLIGONO
        aneis = []
        for anel, buraco in aneis_da_geometria(g2, poligono):
            vivos = [i for i in range(len(anel) - 1)
                     if _mesmo_pt(anel[i], lp) or _mesmo_pt(anel[i + 1], lp)]
            aneis.append((anel, buraco, vivos))
        m.mostrar(lyr, aneis, geom_area=g2 if poligono else None, em_camada=True,
                  total_pt=None if poligono else pt, feicao=(lyr, s["fid"]))

    def canvasPressEvent(self, e):
        if e.button() == QtC.RightButton:
            self.cancelar()
            return
        if e.button() != QtC.LeftButton:
            return
        if self.sel is None:
            lyr = self.camada_editavel()
            if not lyr:
                return
            # a mesma feição destacada (inclusive a escolhida com ↑/↓)
            self._hover_lyr = lyr
            self._definir_candidatos(lyr, e.mapPoint())
            c, _i = self._escolhido()
            if c is None or c["tipo"] == "dentro":
                return
            novo = False
            fid, idx, vm = c["fid"], c["idx"], c["pt"]
            if c["tipo"] == "vertice":
                geom = c["geom"]
            else:
                geom = self._inserir_na_geom(lyr, c["geom"], idx, vm)
                if geom is None:
                    return
                novo = True   # vértice criado: posiciona com as guias e grava ao clicar
            guias, _ = self._montar_guias(lyr, geom, idx)
            # âncora do intervalo quando o vértice anda livre: vizinho anterior (ou o próximo);
            # num ponto, a própria posição original (o passo vale para o deslocamento)
            ancora = vm
            if lyr.geometryType() != PONTO:
                ant, prox = geom.adjacentVertices(idx)
                viz = ant if ant >= 0 else prox
                if viz >= 0:
                    ancora = self.toMapCoordinates(lyr, QgsPointXY(geom.vertexAt(viz)))
            self.sel = dict(lyr=lyr, fid=fid, idx=idx, geom=geom, orig=vm, guias=guias, novo=novo,
                            ancora=ancora, meios_orig=self._meios_originais(lyr, geom, idx, novo))
            self._mostrar_marcas_meio()
            self.rb_hover.reset(POLIGONO)
            self.rb_prev.reset(lyr.geometryType())
            # setColor muda contorno E preenchimento: primeiro ele, depois o preenchimento transparente
            self.rb_prev.setColor(QColor(0, 120, 255, 230))
            if lyr.geometryType() == POLIGONO:
                self.rb_prev.setFillColor(QColor(0, 120, 255, 40))
            elif lyr.geometryType() == PONTO:
                self.rb_prev.setIconSize(12)
            self.pos_press = pos_evento(e)
            self.pegou_no_press = True
            self.canvasMoveEvent(e)
        else:
            self._gravar(e)

    def canvasReleaseEvent(self, e):
        if self.sel is not None and self.pegou_no_press and e.button() == QtC.LeftButton:
            if (pos_evento(e) - self.pos_press).manhattanLength() > 6:
                self._gravar(e)   # modo arrastar
        self.pegou_no_press = False

    def _gravar(self, e):
        s = self.sel
        pt, _rot, _a = self._resolver_sel(e.mapPoint())
        lp = self.toLayerCoordinates(s["lyr"], pt)
        lyr = s["lyr"]
        if s.get("novo"):
            lyr.beginEditCommand("Criar vértice (Guias CAD)")
            g3 = QgsGeometry(s["geom"])
            g3.moveVertex(lp.x(), lp.y(), s["idx"])
            ok = lyr.changeGeometry(s["fid"], g3)
        else:
            lyr.beginEditCommand("Mover vértice (Guias CAD)")
            ok = lyr.moveVertex(lp.x(), lp.y(), s["fid"], s["idx"])
        if ok:
            lyr.endEditCommand()
        else:
            lyr.destroyEditCommand()
            self._msg("Não foi possível mover o vértice.", NIVEL_AVISO)
        lyr.triggerRepaint()
        self.cancelar()

    def cancelar(self):
        self.sel = None
        self.pegou_no_press = False
        self.cands = []          # a geometria pode ter mudado: o próximo movimento do mouse refaz
        self.sob_cursor = set()
        self.hover = self.hover_seg = None
        self.limpar_visual()

    def _apagar_vertice(self):
        """Apaga o vértice selecionado (clicado) ou o que está sob o cursor."""
        if self.sel is not None and self.sel.get("novo"):
            self.cancelar()   # vértice ainda não gravado: só desiste
            return True
        if self.sel is not None:
            lyr, fid, idx, g = self.sel["lyr"], self.sel["fid"], self.sel["idx"], self.sel["geom"]
        elif self.hover is not None:
            lyr, (_d, fid, idx, g, _vm) = self.hover
        else:
            return False
        if not lyr.isEditable():
            return False
        g2 = QgsGeometry(g)
        n = g.constGet().nCoordinates()
        # não deixa a feição ficar inválida (linha < 2 vértices, anel < 3 vértices)
        if not g2.deleteVertex(idx) or g2.isEmpty() or g2.constGet().nCoordinates() != n - 1:
            if lyr.geometryType() == PONTO:
                self._msg("Este é o único ponto da feição: para apagar o ponto inteiro use "
                          "\"Apagar selecionados\" do QGIS.", NIVEL_AVISO)
            else:
                self._msg("Não dá para apagar: a feição ficaria com vértices de menos.", NIVEL_AVISO)
            return True
        lyr.beginEditCommand("Apagar vértice (Guias CAD)")
        if lyr.changeGeometry(fid, g2):
            lyr.endEditCommand()
        else:
            lyr.destroyEditCommand()
            self._msg("Não foi possível apagar o vértice.", NIVEL_AVISO)
        lyr.triggerRepaint()
        self.cancelar()
        self.hover = None
        return True

    def keyPressEvent(self, e):
        if e.key() in (QtC.Key_Up, QtC.Key_Down):
            if self._trocar_feicao(1 if e.key() == QtC.Key_Up else -1):
                # no QGIS, "ignorar" o evento quer dizer que a ferramenta o usou: o mapa não anda
                e.ignore()
            else:
                e.accept()          # sem feições sobrepostas: as setas movem o mapa, como no QGIS
            return
        if e.key() == QtC.Key_Escape:
            self.cancelar()
            e.accept()
            return
        if e.key() in (QtC.Key_Delete, QtC.Key_Backspace):
            self._apagar_vertice()
            e.accept()   # nunca deixa o Delete apagar a feição selecionada
            return
        if self.tecla_comum(e):
            if self.sel is not None:
                lyr, g, idx = self.sel["lyr"], self.sel["geom"], self.sel["idx"]
                self.sel["guias"], _ = self._montar_guias(lyr, g, idx)
                self._mostrar_marcas_meio()      # tecla P mostra/esconde os meios originais
                if self._ult_pt is not None:
                    self._medir(self._previa(self._ult_pt), self._ult_pt)
            e.accept()
            return
        e.ignore()


# =====================================================================
class FerramentaDesenhar(_BaseGuias):
    """Desenha linha/polígono novo com guias de continuação, 90° e 45°
    em relação ao último lado (e ao primeiro lado, para fechar polígonos)."""

    def __init__(self, iface, acao, medidas=None, intervalo=None, meio=None):
        super().__init__(iface, acao, medidas, intervalo, meio)
        self.pts = []
        self.ref = (1.0, 0.0)
        self.tipo = LINHA

    def activate(self):
        super().activate()
        self._status("Desenhar com guias: clique esquerdo adiciona · direito/Enter finaliza · "
                     "Backspace desfaz ponto · Esc cancela · SHIFT livre · G guias · 4 45° · I interseção c/ camadas · "
                     "M medidas · D intervalo · P meio do lado")

    def _guias(self):
        if not self.pts:
            return []
        ult = self.pts[-1]
        gs = []
        if len(self.pts) >= 2:
            pen = self.pts[-2]
            gs += guias_angulares(ult, ult.x() - pen.x(), ult.y() - pen.y(), "",
                                  forte_0=True, forte_90=True, rot0="Continuação")
        else:
            gs += guias_angulares(ult, self.ref[0], self.ref[1], "(eixo)",
                                  forte_0=True, forte_90=True, rot0="0° (eixo)")
        if self.tipo == POLIGONO and len(self.pts) >= 2:
            ini, seg = self.pts[0], self.pts[1]
            gs += guias_angulares(ini, seg.x() - ini.x(), seg.y() - ini.y(), "no início",
                                  rot0="Extensão do 1º lado")
        return self.filtrar(gs)

    def _extras(self):
        ex = []
        if self.tipo == POLIGONO and len(self.pts) >= 3:
            ex.append((self.pts[0], "Ponto inicial"))
        if self._meio_ligado():
            # meio dos lados já desenhados da feição nova (o lado até o cursor ainda muda)
            for a, b in zip(self.pts, self.pts[1:]):
                ex.append((QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2), "Meio do lado (desenho)"))
        return ex

    def _resolver_desenho(self, p, guias):
        """Ponto do cursor com guias e, se ligado, o intervalo de distância a partir do último ponto."""
        pt, rot, ativas = self.resolver(p, guias, self._extras())
        if self.pts and self._pode_passo(rot, ativas):
            ajustado = self._passo(self.pts[-1], pt, ativas[0] if ativas else None,
                                   self.camada_editavel(avisar=False))
            if ajustado is not None:
                pt, rot = ajustado, self._rotulo_passo(rot)
        return pt, rot, ativas

    def canvasMoveEvent(self, e):
        p = e.mapPoint()
        if not self.pts:
            pt, rot, _ = self.resolver(p, [])
            self.mostrar_ponto(pt, rot, pos_evento(e))
            return
        guias = self._guias()
        pt, rot, ativas = self._resolver_desenho(p, guias)
        self._previa(pt)
        self.desenhar_guias(guias, ativas, pt)
        self.mostrar_ponto(pt, rot, pos_evento(e))
        self._ult_pt = QgsPointXY(pt)
        self._medir(pt)

    def _medir(self, cursor=None):
        """Comprimento de cada lado já desenhado, do lado até o cursor (destaque),
        do lado de fechamento (polígono), área do polígono e total da linha."""
        m = self.medidas
        if m is None or not m.ligado:
            return
        lyr = self.camada_editavel(avisar=False)
        pts = list(self.pts)
        com_cursor = cursor is not None and bool(pts) and _dist(cursor, pts[-1]) > 1e-12
        if com_cursor:
            pts.append(QgsPointXY(cursor))
        if lyr is None or len(pts) < 2:
            m.limpar()
            return
        poligono = self.tipo == POLIGONO
        geom, vivos = None, []
        if poligono and len(pts) >= 3:
            anel = pts + [pts[0]]
            if com_cursor:
                vivos = [len(anel) - 3, len(anel) - 2]   # último -> cursor e cursor -> início
            geom = QgsGeometry.fromPolygonXY([anel])
        else:
            anel = pts
            if com_cursor:
                vivos = [len(anel) - 2]
        total_pt = pts[-1] if (not poligono and len(pts) >= 3) else None
        m.mostrar(lyr, [(anel, False, vivos)], geom_area=geom, em_camada=False, total_pt=total_pt)

    def _previa(self, cursor=None):
        self.rb_prev.reset(self.tipo)
        self.rb_prev.setColor(QColor(0, 120, 255, 230))
        if self.tipo == POLIGONO:
            self.rb_prev.setFillColor(QColor(0, 120, 255, 40))
        pts = list(self.pts) + ([cursor] if cursor is not None else [])
        for i, q in enumerate(pts):
            self.rb_prev.addPoint(q, i == len(pts) - 1)

    def canvasPressEvent(self, e):
        if e.button() == QtC.RightButton:
            self._finalizar()
            return
        if e.button() != QtC.LeftButton:
            return
        if not self.pts:
            lyr = self.camada_editavel()
            if not lyr:
                return
            self.tipo = lyr.geometryType()
            # se o 1º ponto cair numa borda, os eixos ficam alinhados com ela
            m = self.canvas().snappingUtils().snapToMap(e.mapPoint())
            self.ref = (1.0, 0.0)
            if m.isValid() and m.hasEdge():
                a, b = m.edgePoints()
                if _dist(QgsPointXY(a), QgsPointXY(b)) > 0:
                    self.ref = (b.x() - a.x(), b.y() - a.y())
            pt, _r, _a = self.resolver(e.mapPoint(), [])
        else:
            pt, _r, _a = self._resolver_desenho(e.mapPoint(), self._guias())
        if self.tipo == POLIGONO and len(self.pts) >= 3 and _dist(pt, self.pts[0]) < 1e-9:
            self._finalizar()   # clicou no ponto inicial: fecha
            return
        if self.pts and _dist(pt, self.pts[-1]) < 1e-9:
            return
        self.pts.append(QgsPointXY(pt))
        self.canvasMoveEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k == QtC.Key_Escape:
            self.cancelar()
        elif k in (QtC.Key_Backspace, QtC.Key_Delete):
            if self.pts:
                self.pts.pop()
                self._previa()
                self.desenhar_guias(self._guias(), [], None)
                self._medir(None)
        elif k in (QtC.Key_Return, QtC.Key_Enter):
            self._finalizar()
        elif self.tecla_comum(e):
            if self.pts:
                self._medir(self._ult_pt)
        else:
            e.ignore()
            return
        e.accept()

    def cancelar(self):
        self.pts = []
        self.limpar_visual()

    def _finalizar(self):
        lyr = self.camada_editavel()
        if not lyr or lyr.geometryType() != self.tipo:
            self.cancelar()
            return
        minimo = 3 if self.tipo == POLIGONO else 2
        if len(self.pts) < minimo:
            if self.pts:
                self._msg("Pontos insuficientes (mínimo %d)." % minimo, NIVEL_AVISO)
            self.cancelar()
            return
        lpts = [self.toLayerCoordinates(lyr, p) for p in self.pts]
        if self.tipo == POLIGONO:
            geom = QgsGeometry.fromPolygonXY([lpts + [lpts[0]]])
        else:
            geom = QgsGeometry.fromPolylineXY(lpts)
        wkb = lyr.wkbType()
        if QgsWkbTypes.isMultiType(wkb):
            geom.convertToMultiType()
        if QgsWkbTypes.hasZ(wkb):
            geom.get().addZValue(0)
        if QgsWkbTypes.hasM(wkb):
            geom.get().addMValue(0)

        f = QgsVectorLayerUtils.createFeature(lyr, geom, {}, lyr.createExpressionContext())
        self.cancelar()

        sup = lyr.editFormConfig().suppress()
        padrao = QgsSettings().value("qgis/digitizing/disable_enter_attribute_values_dialog",
                                     False, type=bool)
        sem_form = (sup == FORM_SUPRIMIR or
                    (sup == FORM_PADRAO and padrao) or
                    lyr.fields().count() == 0)
        if sem_form:
            lyr.beginEditCommand("Nova feição (Guias CAD)")
            if lyr.addFeature(f):
                lyr.endEditCommand()
            else:
                lyr.destroyEditCommand()
        else:
            dlg = self.iface.getFeatureForm(lyr, f)
            dlg.setMode(MODO_ADICIONAR)
            (dlg.exec_ if hasattr(dlg, "exec_") else dlg.exec)()
        lyr.triggerRepaint()
