# -*- coding: utf-8 -*-
"""Atualizador Geocodificação (parte do Ferramentas Solo): dois botões na barra.

- Atualizador Geocodificação: várias regras (campo + expressão) numa camada, com regras salvas em JSON.
- Expressão em várias camadas: a mesma expressão num campo em comum de várias camadas."""
import os

from qgis.PyQt.QtGui import QIcon
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

MENU = "Atualizador Geocodificação"
PASTA = os.path.dirname(__file__)


class AtualizadorGeocodificacao:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar
        self.menu = menu or MENU
        self.acoes = []
        self.dlg = None
        self.dlg_varias = None

    def initGui(self):
        janela = self.iface.mainWindow()
        a1 = QAction(QIcon(os.path.join(PASTA, "icon_atualizador.svg")), "Atualizador Geocodificação", janela)
        a1.setObjectName("mFerramentasSoloAtualizadorGeo")
        a1.setToolTip("Atualizador Geocodificação: várias regras (campo + expressão) numa camada")
        a1.triggered.connect(self.abrir)
        a2 = QAction(QIcon(os.path.join(PASTA, "icon_varias.svg")), "Expressão em várias camadas", janela)
        a2.setObjectName("mFerramentasSoloExpressaoVarias")
        a2.setToolTip("Expressão em várias camadas: marque as camadas, escolha um campo em comum e rode "
                      "a expressão em todas")
        a2.triggered.connect(self.abrir_varias)
        self.acoes = [a1, a2]
        for a in self.acoes:
            if self.toolbar is not None:
                self.toolbar.addAction(a)
            else:
                self.iface.addToolBarIcon(a)
            self.iface.addPluginToVectorMenu(self.menu, a)

    def abrir(self):
        from .atualizador import AtualizadorDialog
        if self.dlg is None:
            self.dlg = AtualizadorDialog(self.iface, abrir_varias=self.abrir_varias)
        self.dlg.refresh_layers()
        self._mostrar(self.dlg)

    def abrir_varias(self):
        from .varias_camadas import ExpressaoVariasCamadasDialog
        if self.dlg_varias is None:
            self.dlg_varias = ExpressaoVariasCamadasDialog(self.iface)
        else:
            self.dlg_varias.atualizar_camadas(marcar_do_painel=True)
        self._mostrar(self.dlg_varias)

    @staticmethod
    def _mostrar(dlg):
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def unload(self):
        for dlg in (self.dlg, self.dlg_varias):
            if dlg is not None:
                try:
                    dlg.close()
                    dlg.deleteLater()
                except RuntimeError:
                    pass
        self.dlg = self.dlg_varias = None
        for a in self.acoes:
            if self.toolbar is not None:
                try:
                    self.toolbar.removeAction(a)
                except RuntimeError:
                    pass
            else:
                self.iface.removeToolBarIcon(a)
            self.iface.removePluginVectorMenu(self.menu, a)
            a.deleteLater()
        self.acoes = []
