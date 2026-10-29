# -*- coding: utf-8 -*-
"""
Núcleo geométrico do plugin Numerar Lotes (sem interface).

Mesma ideia da rotina 1NUMERARLOTES do AutoCAD: cada lote é posicionado
pelo trecho em que encosta no contorno da quadra (a face externa / testada),
medido a partir do ponto inicial, no sentido escolhido. A ordem desses
trechos dá a numeração sequencial; a distância em metros dá a numeração
por perímetro.
"""

import math

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsSpatialIndex,
    QgsWkbTypes,
)

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------
HORARIO = "horario"
ANTI_HORARIO = "anti_horario"

SEQUENCIAL = "sequencial"
PERIMETRO = "perimetro"

INICIO = "inicio"
MEIO = "meio"
FIM = "fim"

# Canto do retângulo envolvente usado quando a quadra não tem ponto clicado
# (fração em X, fração em Y)
CANTOS = {
    "NO": (0.0, 1.0),
    "NE": (1.0, 1.0),
    "SE": (1.0, 0.0),
    "SO": (0.0, 0.0),
}

PASSO_DENSIFICAR = 0.5   # m - discretização da face do lote junto ao contorno
TESTADA_MINIMA = 1.0     # m - encostar menos que isso no contorno não conta como face
DIST_MAX_CLIQUE = 30.0   # m - clique mais longe que isso de qualquer quadra é ignorado


# --------------------------------------------------------------------------
# Compatibilidade entre versões do QGIS
# --------------------------------------------------------------------------
def _enum(caminho_novo, caminho_antigo):
    obj = Qgis
    try:
        for parte in caminho_novo:
            obj = getattr(obj, parte)
        return obj
    except AttributeError:
        obj = caminho_antigo[0]
        for parte in caminho_antigo[1:]:
            obj = getattr(obj, parte)
        return obj


_CAP_FLAT = _enum(("EndCapStyle", "Flat"), (QgsGeometry, "CapFlat"))
_JOIN_MITER = _enum(("JoinStyle", "Miter"), (QgsGeometry, "JoinStyleMiter"))
_TIPO_POLIGONO = _enum(("GeometryType", "Polygon"), (QgsWkbTypes, "PolygonGeometry"))
_TIPO_LINHA = _enum(("GeometryType", "Line"), (QgsWkbTypes, "LineGeometry"))


def _buffer(geom, distancia):
    """Buffer com cantos em esquadro (mitra), para não arredondar as quinas."""
    return geom.buffer(distancia, 4, _CAP_FLAT, _JOIN_MITER, 5.0)


# --------------------------------------------------------------------------
# Utilitários de geometria
# --------------------------------------------------------------------------
def _retificar(geom):
    """Cópia sem curvas (arcos viram segmentos)."""
    g = QgsGeometry(geom)
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g.convertToStraightSegment()
    return g


def partes_poligono(geom):
    """Lista das partes poligonais simples de qualquer geometria."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return []
    g = _retificar(geom)
    partes = []
    for p in g.asGeometryCollection():
        if p.isEmpty():
            continue
        if p.type() != _TIPO_POLIGONO:
            continue
        if p.isMultipart():
            partes.extend(partes_poligono(p))
        else:
            partes.append(p)
    return partes


def _poligono_valido(geom):
    """Geometria poligonal válida (ou None)."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    g = _retificar(geom)
    if not g.isGeosValid():
        g = g.makeValid()
    partes = partes_poligono(g)
    if not partes:
        return None
    if len(partes) == 1:
        return partes[0]
    return QgsGeometry.collectGeometry(partes)


def _linhas(geom):
    """Lista de polilinhas (listas de QgsPointXY) contidas na geometria."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return []
    linhas = []
    for p in geom.asGeometryCollection():
        if p.isEmpty() or p.type() != _TIPO_LINHA:
            continue
        if p.isMultipart():
            linhas.extend(p.asMultiPolyline())
        else:
            linhas.append(p.asPolyline())
    return [l for l in linhas if len(l) >= 2]


def _bordas(geom):
    """Todas as bordas (anéis) de um polígono, como multilinha."""
    aneis = []
    for parte in partes_poligono(geom):
        for anel in parte.asPolygon():
            if len(anel) >= 2:
                aneis.append(anel)
    if not aneis:
        return None
    return QgsGeometry.fromMultiPolylineXY(aneis)


def _anel_externo(geom):
    """Anel externo da maior parte do polígono, sem pontos repetidos, fechado."""
    partes = partes_poligono(geom)
    if not partes:
        return []
    maior = max(partes, key=lambda p: p.area())
    poligono = maior.asPolygon()
    if not poligono:
        return []
    limpo = []
    for p in poligono[0]:
        p = QgsPointXY(p.x(), p.y())
        if not limpo or p.sqrDist(limpo[-1]) > 1e-12:
            limpo.append(p)
    if len(limpo) < 3:
        return []
    if limpo[0].sqrDist(limpo[-1]) > 1e-12:
        limpo.append(QgsPointXY(limpo[0]))
    return limpo


def _area_com_sinal(pts):
    s = 0.0
    for a, b in zip(pts[:-1], pts[1:]):
        s += a.x() * b.y() - b.x() * a.y()
    return s / 2.0


# --------------------------------------------------------------------------
# Geração das quadras a partir dos lotes
# --------------------------------------------------------------------------
def gerar_quadras(geometrias, tolerancia, progresso=None):
    """
    Junta os lotes encostados numa quadra.

    Cada lote ganha um buffer de +tolerancia (cantos em mitra), tudo é unido,
    e a união volta com -tolerancia. Frestas e sobreposições menores que
    2 x tolerancia somem; os furos internos são preenchidos.
    """
    expandidos = []
    total = len(geometrias)
    for i, g in enumerate(geometrias):
        g = _poligono_valido(g)
        if g is None:
            continue
        expandidos.append(_buffer(g, tolerancia) if tolerancia > 0 else g)
        if progresso and i % 200 == 0:
            progresso(int(40 * i / max(total, 1)))
    if not expandidos:
        return []

    uniao = QgsGeometry.unaryUnion(expandidos)
    if progresso:
        progresso(60)

    quadras = []
    for parte in partes_poligono(uniao):
        cheia = parte.removeInteriorRings()
        if tolerancia > 0:
            cheia = _buffer(cheia, -tolerancia)
        for p in partes_poligono(cheia):
            p = p.removeInteriorRings()
            if p.area() > 1e-6:
                quadras.append(p)
    if progresso:
        progresso(70)
    return quadras


# --------------------------------------------------------------------------
# Lotes x quadras
# --------------------------------------------------------------------------
def associar_lotes(lotes, quadras):
    """
    lotes: dict {fid: QgsGeometry}; quadras: lista de QgsGeometry.
    Devolve ({indice_quadra: [fid, ...]}, [fids fora de quadra]).
    O lote vai para a quadra que contém o seu ponto interno; se nenhuma
    contém, para a que tem a maior área em comum com ele.
    """
    indice = QgsSpatialIndex()
    for i, q in enumerate(quadras):
        f = QgsFeature(i)
        f.setGeometry(q)
        indice.addFeature(f)

    grupos = {i: [] for i in range(len(quadras))}
    fora = []
    for fid, g in lotes.items():
        candidatas = indice.intersects(g.boundingBox())
        escolhida = None
        if candidatas:
            ponto = g.pointOnSurface()
            for c in candidatas:
                if quadras[c].contains(ponto):
                    escolhida = c
                    break
            if escolhida is None:
                melhor = 0.0
                for c in candidatas:
                    inter = quadras[c].intersection(g)
                    a = inter.area() if inter and not inter.isEmpty() else 0.0
                    if a > melhor:
                        melhor, escolhida = a, c
                if melhor <= 1e-6:
                    escolhida = None
        if escolhida is None:
            fora.append(fid)
        else:
            grupos[escolhida].append(fid)
    return grupos, fora


# --------------------------------------------------------------------------
# Contorno orientado da quadra
# --------------------------------------------------------------------------
class Contorno:
    """Anel externo da quadra, orientado no sentido pedido, com origem móvel."""

    def __init__(self, quadra, sentido):
        pts = _anel_externo(quadra)
        if pts:
            anti = _area_com_sinal(pts) > 0
            if (sentido == HORARIO and anti) or (sentido == ANTI_HORARIO and not anti):
                pts = list(reversed(pts))
        self.pts = pts
        self.linha = QgsGeometry.fromPolylineXY(pts) if pts else QgsGeometry()
        self.comprimento = self.linha.length() if pts else 0.0
        self.origem = 0.0
        self._buffers = {}
        # distância acumulada até cada vértice
        self.acumulado = [0.0]
        for a, b in zip(pts[:-1], pts[1:]):
            self.acumulado.append(self.acumulado[-1] + math.hypot(b.x() - a.x(), b.y() - a.y()))

    @property
    def valido(self):
        return self.comprimento > 0

    # -- posições ----------------------------------------------------------
    def absoluta(self, ponto):
        return self.linha.lineLocatePoint(QgsGeometry.fromPointXY(ponto))

    def relativa(self, ponto):
        L = self.comprimento
        r = (self.absoluta(ponto) - self.origem) % L
        if L - r < 1e-7:
            r = 0.0
        return r

    def ponto_em(self, relativa):
        g = self.linha.interpolate((relativa + self.origem) % self.comprimento)
        return g.asPoint()

    def distancia(self, ponto):
        return self.linha.distance(QgsGeometry.fromPointXY(ponto))

    # -- origem ------------------------------------------------------------
    def origem_no_ponto(self, ponto, tolerancia_vertice=0.0):
        """Projeta o ponto no contorno; perto de um vértice, gruda nele."""
        melhor_i, melhor_d = None, None
        for i, v in enumerate(self.pts[:-1]):
            d = math.hypot(v.x() - ponto.x(), v.y() - ponto.y())
            if melhor_d is None or d < melhor_d:
                melhor_i, melhor_d = i, d
        if melhor_i is not None and melhor_d <= tolerancia_vertice:
            self.origem = self.acumulado[melhor_i]
        else:
            self.origem = self.absoluta(ponto)

    def origem_no_canto(self, canto):
        """Origem no vértice mais próximo do canto do retângulo envolvente."""
        fx, fy = CANTOS.get(canto, CANTOS["NO"])
        bb = self.linha.boundingBox()
        alvo = QgsPointXY(bb.xMinimum() + fx * bb.width(), bb.yMinimum() + fy * bb.height())
        melhor_i = min(
            range(len(self.pts) - 1),
            key=lambda i: self.pts[i].sqrDist(alvo),
        )
        self.origem = self.acumulado[melhor_i]

    def trecho_inicial(self, comprimento):
        """Pontos do contorno da origem até 'comprimento' à frente (seta de sentido)."""
        L = self.comprimento
        comprimento = min(comprimento, L * 0.95)
        n = max(2, int(comprimento / 0.5) + 1)
        pts = []
        # inclui os vértices do caminho para a seta seguir as quinas
        marcas = [comprimento * k / (n - 1) for k in range(n)]
        for v in self.acumulado[:-1]:
            r = (v - self.origem) % L
            if 0 < r < comprimento:
                marcas.append(r)
        for r in sorted(set(marcas)):
            pts.append(self.ponto_em(r))
        return pts

    # -- face externa ------------------------------------------------------
    def _buffer_anel(self, distancia):
        chave = round(distancia, 6)
        if chave not in self._buffers:
            self._buffers[chave] = self.linha.buffer(max(distancia, 1e-4), 4)
        return self._buffers[chave]

    def testadas(self, lote, distancia_max, testada_minima=TESTADA_MINIMA,
                 passo=PASSO_DENSIFICAR):
        """
        Trechos do contorno (em distância a partir da origem) onde o lote
        encosta. Lista de (inicio, fim) ordenada; o primeiro trecho pode
        começar negativo quando o lote passa por cima do ponto inicial.
        """
        L = self.comprimento
        if L <= 0:
            return []
        borda = _bordas(lote)
        if borda is None:
            return []
        perto = borda.intersection(self._buffer_anel(distancia_max))
        if perto is None or perto.isEmpty():
            return []
        perto = perto.densifyByDistance(passo)

        intervalos = []
        for linha in _linhas(perto):
            pos = [self.relativa(p) for p in linha]
            for a, b in zip(pos[:-1], pos[1:]):
                lo, hi = (a, b) if a <= b else (b, a)
                if hi - lo > L / 2.0:          # segmento atravessa a origem
                    intervalos.append((hi, L))
                    intervalos.append((0.0, lo))
                else:
                    intervalos.append((lo, hi))
        if not intervalos:
            return []

        intervalos.sort()
        junta = distancia_max + passo
        trechos = []
        for a, b in intervalos:
            if trechos and a <= trechos[-1][1] + junta:
                if b > trechos[-1][1]:
                    trechos[-1][1] = b
            else:
                trechos.append([a, b])

        # encostar só num canto / num pedacinho não é face externa
        trechos = [t for t in trechos if t[1] - t[0] >= testada_minima]
        if not trechos:
            return []

        # lote que passa pela origem: junta o fim do contorno com o começo
        if len(trechos) > 1 and trechos[0][0] <= junta and trechos[-1][1] >= L - junta:
            ultimo = trechos.pop()
            trechos[0][0] = ultimo[0] - L

        trechos.sort()
        return [(a, b) for a, b in trechos]


# --------------------------------------------------------------------------
# Numeração
# --------------------------------------------------------------------------
class Parametros:
    def __init__(self, sentido=HORARIO, tipo=SEQUENCIAL, inicial=1, incremento=1,
                 ponto_testada=FIM, numerar_internos=False, distancia_face=0.30,
                 testada_minima=TESTADA_MINIMA):
        self.sentido = sentido
        self.tipo = tipo
        self.inicial = inicial
        self.incremento = incremento
        self.ponto_testada = ponto_testada
        self.numerar_internos = numerar_internos
        self.distancia_face = distancia_face
        self.testada_minima = testada_minima


def _arredondar(v):
    return int(math.floor(v + 0.5))


def numerar_quadra(contorno, lotes, params):
    """
    contorno: Contorno com a origem já definida.
    lotes: lista de (fid, QgsGeometry).
    Devolve (numeros {fid: int}, fids_sem_numero, ordem [fid...]).
    """
    com_face = []
    internos = []
    for fid, g in lotes:
        trechos = contorno.testadas(g, params.distancia_face, params.testada_minima)
        if trechos:
            a, b = trechos[0]
            com_face.append((a, b, fid))
        else:
            internos.append((fid, g))

    com_face.sort(key=lambda t: (t[0], t[1]))
    numeros = {}
    ordem = []

    if params.tipo == SEQUENCIAL:
        n = params.inicial
        for a, b, fid in com_face:
            numeros[fid] = n
            ordem.append(fid)
            n += params.incremento
        sem_numero = []
        if params.numerar_internos and internos:
            internos.sort(key=lambda t: contorno.relativa(t[1].pointOnSurface().asPoint()))
            for fid, g in internos:
                numeros[fid] = n
                ordem.append(fid)
                n += params.incremento
        else:
            sem_numero = [fid for fid, g in internos]
    else:
        for a, b, fid in com_face:
            if params.ponto_testada == INICIO:
                v = a
            elif params.ponto_testada == MEIO:
                v = (a + b) / 2.0
            else:
                v = b
            numeros[fid] = max(1, _arredondar(max(v, 0.0)))
            ordem.append(fid)
        sem_numero = [fid for fid, g in internos]

    return numeros, sem_numero, ordem


def quadra_mais_proxima(ponto, contornos, quadras):
    """Índice da quadra que contém o ponto ou cujo contorno está mais perto."""
    pg = QgsGeometry.fromPointXY(ponto)
    melhor, melhor_d = None, None
    for i, c in contornos.items():
        if quadras[i].contains(pg):
            return i
        d = c.distancia(ponto)
        if melhor_d is None or d < melhor_d:
            melhor, melhor_d = i, d
    if melhor_d is not None and melhor_d <= DIST_MAX_CLIQUE:
        return melhor
    return None


def definir_origens(contornos, quadras, pontos, canto_padrao):
    """
    pontos: lista de (QgsPointXY, tolerancia_vertice) já na SRC dos lotes.
    Quadra com ponto clicado usa o último clique dela; as demais, o canto.
    Devolve {indice_quadra: 'clique' | 'canto'}.
    """
    escolhidos = {}
    for ponto, tol in pontos:
        i = quadra_mais_proxima(ponto, contornos, quadras)
        if i is not None:
            escolhidos[i] = (ponto, tol)
    origem = {}
    for i, c in contornos.items():
        if i in escolhidos:
            c.origem_no_ponto(*escolhidos[i])
            origem[i] = "clique"
        else:
            c.origem_no_canto(canto_padrao)
            origem[i] = "canto"
    return origem
