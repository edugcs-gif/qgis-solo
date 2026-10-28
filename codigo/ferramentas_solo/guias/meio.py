# -*- coding: utf-8 -*-
"""Aderência no meio do lado (ponto médio de cada face) nas ferramentas de mover vértice e desenhar.

Com ela ligada, o cursor gruda no ponto médio dos lados das feições visíveis (linhas e
polígonos) e, ao desenhar, também no meio dos lados já desenhados. Vértice continua com
prioridade; SHIFT deixa livre. Botão na barra liga/desliga (fica salvo); tecla P dentro
das ferramentas também.
"""
import os

from qgis.PyQt.QtGui import QIcon
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6
from qgis.core import QgsSettings

CHAVE = "FerramentasSolo/meio_lado_ligado"
TITULO = "Aderência no meio do lado"
ICONE = os.path.join(os.path.dirname(__file__), "icons", "meio.svg")


class AderenciaMeio:

    def __init__(self, iface):
        self.iface = iface
        self.ligado = QgsSettings().value(CHAVE, True, type=bool)
        self.acao = None

    def criar_acao(self, parent):
        self.acao = QAction(QIcon(ICONE), TITULO, parent)
        self.acao.setObjectName("mFerramentasSoloMeioLado")
        self.acao.setCheckable(True)
        self.acao.setChecked(self.ligado)
        self.acao.toggled.connect(self._ao_alternar)
        self._dica()
        return self.acao

    def _dica(self):
        if self.acao is not None:
            self.acao.setToolTip(
                "%s: %s\nO cursor gruda no ponto médio de cada lado ao mover/criar vértice e ao "
                "desenhar.\nTecla P dentro das ferramentas · SHIFT deixa livre"
                % (TITULO, "Ligada" if self.ligado else "Desligada"))

    def _ao_alternar(self, marcado):
        self.ligado = bool(marcado)
        QgsSettings().setValue(CHAVE, self.ligado)
        self._dica()

    def alternar(self):
        """Tecla P: inverte e devolve o novo estado."""
        if self.acao is not None:
            self.acao.setChecked(not self.ligado)   # dispara _ao_alternar
        else:
            self._ao_alternar(not self.ligado)
        return self.ligado

    def remover(self):
        if self.acao is not None:
            try:
                self.acao.deleteLater()
            except RuntimeError:
                pass
            self.acao = None
