# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

NOME = "Seleção por sobreposição"


class SelecaoSobreposicao:

    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar          # barra do Ferramentas Solo
        self.menu = menu or ("&" + NOME)
        self.acao = None
        self.dlg = None

    def initGui(self):
        icone = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.acao = QAction(icone, NOME + "...", self.iface.mainWindow())
        self.acao.setObjectName("mFerramentasSoloSelecaoSobreposicao")
        self.acao.setToolTip("Seleciona polígonos pelo % de área sobreposta a outra camada")
        self.acao.triggered.connect(self.abrir)
        if self.toolbar is not None:
            self.toolbar.addAction(self.acao)
        else:
            self.iface.addToolBarIcon(self.acao)
        self.iface.addPluginToVectorMenu(self.menu, self.acao)

    def unload(self):
        if self.acao is not None:
            self.iface.removePluginVectorMenu(self.menu, self.acao)
            if self.toolbar is not None:
                try:
                    self.toolbar.removeAction(self.acao)
                except RuntimeError:
                    pass
            else:
                self.iface.removeToolBarIcon(self.acao)
            self.acao.deleteLater()
            self.acao = None
        if self.dlg is not None:
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None

    def abrir(self):
        from .dialog import DialogoSobreposicao
        if self.dlg is None:
            self.dlg = DialogoSobreposicao(self.iface)
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
