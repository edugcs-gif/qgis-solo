# -*- coding: utf-8 -*-
"""Numerar Lotes (parte do Ferramentas Solo): numera os lotes de cada quadra pelo contorno,
com sentido, tipo de numeração e ponto inicial escolhidos. Versão QGIS da rotina 1NUMERARLOTES."""
import os

from qgis.PyQt.QtGui import QIcon
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

from .dialogo import DialogoNumerarLotes, TITULO

MENU = "Numerar Lotes"


class NumerarLotesPlugin:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar
        self.menu = menu or MENU
        self.acao = None
        self.dialogo = None

    def initGui(self):
        icone = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.acao = QAction(icone, TITULO, self.iface.mainWindow())
        self.acao.setObjectName("mFerramentasSoloNumerarLotes")
        self.acao.setToolTip("Numerar Lotes: numera os lotes de cada quadra pelo contorno")
        self.acao.triggered.connect(self.abrir)
        if self.toolbar is not None:
            self.toolbar.addAction(self.acao)
        else:
            self.iface.addToolBarIcon(self.acao)
        self.iface.addPluginToVectorMenu(self.menu, self.acao)

    def unload(self):
        if self.dialogo is not None:
            try:
                self.dialogo.remover_do_mapa()
                self.dialogo.close()
                self.dialogo.deleteLater()
            except RuntimeError:
                pass
            self.dialogo = None
        if self.acao is not None:
            if self.toolbar is not None:
                try:
                    self.toolbar.removeAction(self.acao)
                except RuntimeError:
                    pass
            else:
                self.iface.removeToolBarIcon(self.acao)
            self.iface.removePluginVectorMenu(self.menu, self.acao)
            self.acao.deleteLater()
            self.acao = None

    def abrir(self):
        if self.dialogo is None:
            self.dialogo = DialogoNumerarLotes(self.iface)
        self.dialogo.show()
        self.dialogo.raise_()
        self.dialogo.activateWindow()
