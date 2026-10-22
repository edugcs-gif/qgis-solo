# -*- coding: utf-8 -*-
"""Importar/Exportar DXF (parte do Ferramentas Solo): dois botões na barra."""
import os

from qgis.PyQt.QtGui import QIcon
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

from .dialog import DialogoImportarCAD
from .exportador import exportar_selecao, OUTPUT_FILE

MENU = "Importar/Exportar DXF"


def _icone(nome):
    caminho = os.path.join(os.path.dirname(__file__), nome)
    return QIcon(caminho) if os.path.exists(caminho) else QIcon()


class ImportarExportarDXF:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar          # barra do Ferramentas Solo
        self.menu = menu or MENU
        self.actions = []

    def initGui(self):
        janela = self.iface.mainWindow()

        acao_importar = QAction(_icone("icon_importar.svg"), "Importar CAD (DXF)", janela)
        acao_importar.setToolTip(
            "Importar Pontos/Vértices/Blocos/Círculos, Textos, Linhas ou Polígonos de um DXF"
        )
        acao_importar.setObjectName("mFerramentasSoloImportarDXF")
        acao_importar.triggered.connect(self.importar)

        acao_exportar = QAction(_icone("icon_exportar.svg"), "Exportar seleção para DXF", janela)
        acao_exportar.setToolTip("Exporta os polígonos selecionados para:\n" + OUTPUT_FILE)
        acao_exportar.setObjectName("mFerramentasSoloExportarDXF")
        acao_exportar.triggered.connect(self.exportar)

        self.actions = [acao_importar, acao_exportar]
        for acao in self.actions:
            if self.toolbar is not None:
                self.toolbar.addAction(acao)
            else:
                self.iface.addToolBarIcon(acao)
            self.iface.addPluginToVectorMenu(self.menu, acao)

    def unload(self):
        for acao in self.actions:
            if self.toolbar is not None:
                try:
                    self.toolbar.removeAction(acao)
                except RuntimeError:
                    pass
            else:
                self.iface.removeToolBarIcon(acao)
            self.iface.removePluginVectorMenu(self.menu, acao)
            acao.deleteLater()
        self.actions = []

    def importar(self):
        dlg = DialogoImportarCAD(self.iface.mainWindow())
        (dlg.exec_ if hasattr(dlg, "exec_") else dlg.exec)()

    def exportar(self):
        exportar_selecao(self.iface)
