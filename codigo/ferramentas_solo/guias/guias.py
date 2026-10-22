# -*- coding: utf-8 -*-
"""Geometria das guias (linhas infinitas) usadas pelas ferramentas.

Tudo em coordenadas do mapa (CRS do canvas).
"""
import math

from qgis.core import QgsPointXY

EPS = 1e-12


class Guia:
    """Linha-guia infinita: origem + direção unitária."""

    def __init__(self, origem, dx, dy, rotulo, forte=False, g45=False):
        n = math.hypot(dx, dy)
        self.o = QgsPointXY(origem)
        self.dx = dx / n
        self.dy = dy / n
        self.rotulo = rotulo
        self.forte = forte      # sempre desenhada
        self.g45 = g45          # guia de 45° (pode ser desligada com a tecla 4)

    def projetar(self, p):
        """Projeção ortogonal de p na guia -> (ponto, distância)."""
        t = (p.x() - self.o.x()) * self.dx + (p.y() - self.o.y()) * self.dy
        q = QgsPointXY(self.o.x() + t * self.dx, self.o.y() + t * self.dy)
        return q, math.hypot(p.x() - q.x(), p.y() - q.y())

    def distancia(self, p):
        return self.projetar(p)[1]

    def mesma_linha(self, outra, tol):
        cruz = self.dx * outra.dy - self.dy * outra.dx
        return abs(cruz) < 1e-9 and outra.distancia(self.o) < tol

    def extremos(self, comprimento):
        L = comprimento
        return (QgsPointXY(self.o.x() - self.dx * L, self.o.y() - self.dy * L),
                QgsPointXY(self.o.x() + self.dx * L, self.o.y() + self.dy * L))


def nova_guia(origem, dx, dy, rotulo, forte=False, g45=False):
    if math.hypot(dx, dy) < EPS:
        return None
    return Guia(origem, dx, dy, rotulo, forte, g45)


def girar(dx, dy, graus):
    a = math.radians(graus)
    c, s = math.cos(a), math.sin(a)
    return dx * c - dy * s, dx * s + dy * c


def intersecao(g1, g2):
    """Interseção de duas guias infinitas (None se paralelas)."""
    cruz = g1.dx * g2.dy - g1.dy * g2.dx
    if abs(cruz) < 1e-9:
        return None
    wx, wy = g2.o.x() - g1.o.x(), g2.o.y() - g1.o.y()
    t = (wx * g2.dy - wy * g2.dx) / cruz
    return QgsPointXY(g1.o.x() + t * g1.dx, g1.o.y() + t * g1.dy)


def intersecao_segmento(g, a, b):
    """Interseção da guia com o segmento a-b (None se fora do segmento)."""
    sx, sy = b.x() - a.x(), b.y() - a.y()
    cruz = g.dx * sy - g.dy * sx
    if abs(cruz) < 1e-12:
        return None
    wx, wy = a.x() - g.o.x(), a.y() - g.o.y()
    u = (wx * g.dy - wy * g.dx) / cruz   # parâmetro no segmento
    if u < -1e-9 or u > 1 + 1e-9:
        return None
    return QgsPointXY(a.x() + u * sx, a.y() + u * sy)


def guias_angulares(origem, dx, dy, nome, forte_0=False, forte_90=False,
                    rot0=None):
    """Guias a 0°, 90°, 45° e 135° em relação à direção (dx, dy)."""
    out = [
        nova_guia(origem, dx, dy, rot0 or ("Alinhado " + nome), forte=forte_0),
        nova_guia(origem, *girar(dx, dy, 90), ("90° " + nome).strip(), forte=forte_90),
        nova_guia(origem, *girar(dx, dy, 45), ("45° " + nome).strip(), g45=True),
        nova_guia(origem, *girar(dx, dy, 135), ("135° " + nome).strip(), g45=True),
    ]
    return [g for g in out if g]


def intersecao_segmentos(a, b, c, d):
    """Interseção dos segmentos a-b e c-d (None se não se cruzam)."""
    rx, ry = b.x() - a.x(), b.y() - a.y()
    sx, sy = d.x() - c.x(), d.y() - c.y()
    cruz = rx * sy - ry * sx
    if abs(cruz) < 1e-12:
        return None
    wx, wy = c.x() - a.x(), c.y() - a.y()
    t = (wx * sy - wy * sx) / cruz
    u = (wx * ry - wy * rx) / cruz
    if t < -1e-9 or t > 1 + 1e-9 or u < -1e-9 or u > 1 + 1e-9:
        return None
    return QgsPointXY(a.x() + t * rx, a.y() + t * ry)


def dist_ponto_segmento(p, a, b):
    sx, sy = b.x() - a.x(), b.y() - a.y()
    L2 = sx * sx + sy * sy
    if L2 < EPS:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = max(0.0, min(1.0, ((p.x() - a.x()) * sx + (p.y() - a.y()) * sy) / L2))
    return math.hypot(p.x() - (a.x() + t * sx), p.y() - (a.y() + t * sy))
