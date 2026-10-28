# -*- coding: utf-8 -*-
"""Utilidades comuns do Atualizador Geocodificação e da Expressão em várias camadas."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAbstractItemView, QHeaderView, QMessageBox
from qgis.core import (Qgis, QgsExpressionContext, QgsExpressionContextUtils, QgsFields,
                       QgsProject, QgsVectorDataProvider, QgsVectorLayer)

from ..guias.compat import _pegar

# enums do Qt5 (Qt.Checked) e do Qt6 (Qt.CheckState.Checked)
MARCADO = _pegar(Qt, "CheckState.Checked", "Checked")
DESMARCADO = _pegar(Qt, "CheckState.Unchecked", "Unchecked")
ITEM_MARCAVEL = _pegar(Qt, "ItemFlag.ItemIsUserCheckable", "ItemIsUserCheckable")
ITEM_ATIVO = _pegar(Qt, "ItemFlag.ItemIsEnabled", "ItemIsEnabled")
ITEM_SELECIONAVEL = _pegar(Qt, "ItemFlag.ItemIsSelectable", "ItemIsSelectable")
PAPEL_DADO = _pegar(Qt, "ItemDataRole.UserRole", "UserRole")
PAPEL_NOME = int(getattr(PAPEL_DADO, "value", PAPEL_DADO)) + 1     # 2º dado guardado no item
MODAL_JANELA = _pegar(Qt, "WindowModality.WindowModal", "WindowModal")
HORIZONTAL = _pegar(Qt, "Orientation.Horizontal", "Horizontal")
AJUSTAR = _pegar(QHeaderView, "ResizeMode.ResizeToContents", "ResizeToContents")
ESTICAR = _pegar(QHeaderView, "ResizeMode.Stretch", "Stretch")
LINHAS = _pegar(QAbstractItemView, "SelectionBehavior.SelectRows", "SelectRows")
SIM = _pegar(QMessageBox, "StandardButton.Yes", "Yes")
NAO = _pegar(QMessageBox, "StandardButton.No", "No")


def executar(dlg):
    return (dlg.exec_ if hasattr(dlg, "exec_") else dlg.exec)()


def contexto(camada, feicao=None):
    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(camada))
    if feicao is not None:
        ctx.setFeature(feicao)
    return ctx


def nulo(v):
    if v is None:
        return True
    try:
        from qgis.core import QgsVariantUtils
        return QgsVariantUtils.isNull(v)
    except Exception:
        return hasattr(v, "isNull") and v.isNull()


def iguais(a, b):
    if nulo(a) or nulo(b):
        return nulo(a) and nulo(b)
    return a == b


def camadas_vetoriais():
    """Camadas vetoriais válidas na ordem do painel de camadas, com o caminho de grupos."""
    raiz = QgsProject.instance().layerTreeRoot()
    saida = []
    for no in raiz.findLayers():
        lyr = no.layer()
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            continue
        grupos, pai = [], no.parent()
        while pai is not None and pai is not raiz:
            grupos.insert(0, pai.name())
            pai = pai.parent()
        saida.append((lyr, " / ".join(grupos)))
    return saida


def _origens_bloqueadas():
    bloq = set()
    for nome in ("Join", "Expression"):
        for caminho in ("FieldOrigin." + nome, ):
            try:
                bloq.add(_pegar(Qgis, caminho))
            except AttributeError:
                pass
        v = getattr(QgsFields, "Origin" + nome, None)
        if v is not None:
            bloq.add(v)
    return bloq


ORIGENS_BLOQUEADAS = _origens_bloqueadas()


def campo_gravavel(camada, idx):
    """Campo que dá para preencher: não é virtual, nem de união (join), nem a chave (fid)."""
    if idx < 0:
        return False
    if camada.fields().fieldOrigin(idx) in ORIGENS_BLOQUEADAS:
        return False
    try:
        if idx in camada.primaryKeyAttributes():
            return False
    except Exception:
        pass
    return True


def camada_aceita_edicao(camada):
    try:
        capac = camada.dataProvider().capabilities()
        return bool(capac & QgsVectorDataProvider.ChangeAttributeValues)
    except Exception:
        return True


def tipo_amigavel(campo):
    try:
        return campo.friendlyTypeString()
    except Exception:
        return campo.typeName()
