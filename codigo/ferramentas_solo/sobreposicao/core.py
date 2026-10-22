# -*- coding: utf-8 -*-
"""
Núcleo do cálculo de sobreposição (sem interface).

calcular_sobreposicao() devolve {fid: percentual} para cada polígono avaliado
da camada alvo. A interface decide o que selecionar a partir disso.
"""

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsSpatialIndex,
    QgsWkbTypes,
)

# Base do percentual
BASE_ALVO = "alvo"        # área sobreposta / área do polígono da camada a selecionar
BASE_REF = "ref"          # área sobreposta / área do polígono da outra camada
BASE_MAIOR = "maior"      # o maior dos dois

EPS = 1e-9


class Cancelado(Exception):
    pass


def eh_poligono(camada):
    try:
        return camada is not None and camada.geometryType() == QgsWkbTypes.PolygonGeometry
    except Exception:
        return False


def _geom_valida(g):
    """Corrige geometria inválida (autointerseção, anel invertido...)."""
    if g is None or g.isEmpty():
        return None
    if not g.isGeosValid():
        g2 = g.makeValid()
        if g2 is not None and not g2.isEmpty():
            g = g2
    return g


def _area_poligonal(g):
    """Área só das partes de polígono (interseção pode devolver linha/ponto junto)."""
    if g is None or g.isEmpty():
        return 0.0
    return max(g.area(), 0.0)


def calcular_sobreposicao(alvo, ref, ids_alvo=None, so_selecionadas_ref=False,
                          base=BASE_ALVO, somar=True, progresso=None):
    """
    alvo                 camada de polígonos onde a seleção será feita
    ref                  camada de polígonos de comparação (pode ser a mesma)
    ids_alvo             None = todos; senão, só esses fids do alvo são avaliados
    so_selecionadas_ref  usa só as feições selecionadas da camada de comparação
    base                 BASE_ALVO | BASE_REF | BASE_MAIOR
    somar                (base alvo) soma a sobreposição de várias feições da
                         outra camada, sem contar duas vezes onde elas se sobrepõem
    progresso(pct, txt)  callback; se devolver True, cancela

    Retorna {fid: percentual 0..100}
    """
    mesma_camada = alvo.id() == ref.id()

    xform = None
    if ref.crs() != alvo.crs():
        xform = QgsCoordinateTransform(ref.crs(), alvo.crs(), QgsProject.instance())

    # ---- 1. carrega a camada de comparação num índice espacial -------------
    req_ref = QgsFeatureRequest().setNoAttributes()
    if so_selecionadas_ref:
        feats_ref = ref.getSelectedFeatures(req_ref)
        total_ref = ref.selectedFeatureCount()
    else:
        feats_ref = ref.getFeatures(req_ref)
        total_ref = max(ref.featureCount(), 0)

    indice = QgsSpatialIndex()
    geoms_ref = {}
    areas_ref = {}
    for n, f in enumerate(feats_ref):
        if progresso and n % 200 == 0:
            pct = 30.0 * n / total_ref if total_ref else 0
            if progresso(pct, "Lendo camada de comparação..."):
                raise Cancelado()
        if not f.hasGeometry():
            continue
        g = QgsGeometry(f.geometry())
        if xform is not None:
            try:
                g.transform(xform)
            except Exception:
                continue
        g = _geom_valida(g)
        if g is None:
            continue
        a = _area_poligonal(g)
        if a <= 0:
            continue
        geoms_ref[f.id()] = g
        areas_ref[f.id()] = a
        f.setGeometry(g)
        indice.addFeature(f)

    # ---- 2. percorre a camada alvo -----------------------------------------
    req_alvo = QgsFeatureRequest().setNoAttributes()
    if ids_alvo is not None:
        ids_alvo = list(ids_alvo)
        req_alvo.setFilterFids(ids_alvo)
        total_alvo = len(ids_alvo)
    else:
        total_alvo = max(alvo.featureCount(), 0)

    resultado = {}
    for n, f in enumerate(alvo.getFeatures(req_alvo)):
        if progresso and n % 100 == 0:
            pct = 30.0 + 70.0 * n / total_alvo if total_alvo else 30.0
            if progresso(pct, "Calculando sobreposição..."):
                raise Cancelado()

        fid = f.id()
        resultado[fid] = 0.0
        if not f.hasGeometry():
            continue
        g = _geom_valida(QgsGeometry(f.geometry()))
        if g is None:
            continue
        area_alvo = _area_poligonal(g)
        if area_alvo <= 0:
            continue

        candidatos = indice.intersects(g.boundingBox())
        if not candidatos:
            continue

        motor = QgsGeometry.createGeometryEngine(g.constGet())
        motor.prepareGeometry()

        inters = []          # geometrias de interseção (para somar sem duplicar)
        maior_alvo = 0.0     # maior % sobre o alvo com uma única feição
        maior_ref = 0.0      # maior % sobre a feição da outra camada
        for cid in candidatos:
            if mesma_camada and cid == fid:
                continue
            rg = geoms_ref.get(cid)
            if rg is None or not motor.intersects(rg.constGet()):
                continue
            inter = g.intersection(rg)
            a = _area_poligonal(inter)
            if a <= 0:
                continue  # só encosta (divisa comum), não sobrepõe
            inters.append(inter)
            maior_alvo = max(maior_alvo, a / area_alvo)
            maior_ref = max(maior_ref, a / areas_ref[cid])

        if not inters:
            continue

        if somar and len(inters) > 1:
            uniao = QgsGeometry.unaryUnion(inters)
            pct_alvo = _area_poligonal(uniao) / area_alvo
        else:
            pct_alvo = maior_alvo

        if base == BASE_ALVO:
            p = pct_alvo
        elif base == BASE_REF:
            p = maior_ref
        else:
            p = max(pct_alvo, maior_ref)

        resultado[fid] = min(p * 100.0, 100.0)

    if progresso:
        progresso(100.0, "Pronto")
    return resultado


def atende(pct, operador, limite, ignorar_zero=True):
    """Aplica a regra. operador: '>', '>=', '<', '<='."""
    if operador == ">":
        return pct > limite + EPS
    if operador == ">=":
        return pct >= limite - EPS
    if ignorar_zero and pct <= EPS:
        return False
    if operador == "<":
        return pct < limite - EPS
    if operador == "<=":
        return pct <= limite + EPS
    return False
