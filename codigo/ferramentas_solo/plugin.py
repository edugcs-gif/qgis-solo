# -*- coding: utf-8 -*-
"""Ferramentas Solo
Barra de complementos: Desconto de Beiral, Remover Filetes, Guias CAD (com medidas ao vivo),
Comprimento e Área, Preencher Campo, Seleção por Sobreposição, Importar/Exportar DXF e
Relatório de Produtividade. Todo botão da barra aceita atalho em
Configurações > Atalhos de teclado.
Caixa de Ferramentas de Processamento: provedor "Ferramentas Solo" com os scripts da Solo.

Autor: Edu Gabriel Castilho Sembalista.
Menção honrosa: Joel Victor Feitosa da Silva, autor do "Comprimento e Área" original."""
import importlib
import os

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsApplication, QgsMessageLog
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

from .beiral.plugin import DescontoBeiralPlugin
from .filetes.plugin import RemoverFiletes
from .guias.compat import NIVEL_AVISO
from .guias.ferramentas import FerramentaDesenhar, FerramentaMoverVertice
from .guias.intervalo import IntervaloDistancia
from .guias.meio import AderenciaMeio
from .medidas.ao_vivo import MedidasAoVivo
from .medidas.comprimento import ComprimentoSegmentos

PASTA = os.path.dirname(__file__)
MENU = "&Ferramentas Solo"
TITULO = "Ferramentas Solo"

# complementos que viraram parte deste (se continuarem ativos, duplicam botões e atalhos)
ANTIGOS = {
    "ferramentas_cad": "Ferramentas CAD",
    "comprimento_segmentos": "Comprimento e Área",
    "importar_exportar_dxf": "Importar/Exportar DXF",
    "importador_cad": "Importador CAD",
    "relatorio_producao": "Relatório de Produtividade",
    "preencher_campo": "Preencher Campo",
    "selecao_sobreposicao": "Seleção por Sobreposição",
    "atualizador_geocodificacao": "Atualizador Geocodificação",
    "numerar_lotes": "Numerar Lotes",
}

# complementos extras da barra, na ordem dos botões: (módulo, classe, separador antes)
EXTRAS = (
    ("preencher.plugin", "PreencherCampoPlugin", True),
    ("sobreposicao.plugin", "SelecaoSobreposicao", False),
    ("geocodificacao.plugin", "AtualizadorGeocodificacao", False),
    ("numerar.plugin", "NumerarLotesPlugin", False),
    ("dxf.plugin", "ImportarExportarDXF", True),
    ("relatorio.plugin", "RelatorioProducaoPlugin", True),
)


class FerramentasSolo:
    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.beiral = None
        self.filetes = None
        self.comprimento = None
        self.medidas = None
        self.intervalo = None
        self.meio = None
        self.extras = {}
        self.provedor = None
        self.acoes = []
        self.ferramentas = []
        self.atalhos = []          # botões que este plugin registrou em Atalhos de teclado

    # ---------- Caixa de Ferramentas de Processamento ----------
    def initProcessing(self):
        if self.provedor is not None:
            return
        try:
            from .processamento.provider import ProvedorSolo
            self.provedor = ProvedorSolo()
            QgsApplication.processingRegistry().addProvider(self.provedor)
        except Exception as exc:   # um problema na Caixa não pode derrubar a barra
            self.provedor = None
            self._log("Não foi possível carregar os algoritmos de Processamento: %s" % exc)

    def _log(self, texto):
        QgsMessageLog.logMessage(texto, TITULO, NIVEL_AVISO)

    # ---------- barra ----------
    def initGui(self):
        self.initProcessing()
        self.toolbar = self.iface.addToolBar(TITULO)
        self.toolbar.setObjectName("FerramentasSolo")

        # Desconto de Beiral (+ Restaurar original)
        self.beiral = DescontoBeiralPlugin(self.iface, toolbar=self.toolbar, menu=MENU)
        self.beiral.initGui()

        # Remover Filetes
        self.filetes = RemoverFiletes(self.iface, toolbar=self.toolbar, menu=MENU)
        self.filetes.initGui()
        self.toolbar.addSeparator()

        # Comprimento e Área: criado antes das ferramentas porque as medidas ao vivo
        # usam as mesmas opções (casas decimais, vírgula, fonte, elipsoidal)
        self.comprimento = ComprimentoSegmentos(self.iface, toolbar=self.toolbar, menu=MENU)
        self.medidas = MedidasAoVivo(self.iface, self.comprimento)
        self.intervalo = IntervaloDistancia(self.iface, self.comprimento)
        self.meio = AderenciaMeio(self.iface)

        # Guias CAD (mover/criar/apagar vértice e desenhar), com medidas ao vivo
        defs = [
            ("mFerramentasSoloMoverVertice", "mover.svg",
             "Mover / criar / apagar vértice (ou ponto) com guias (extend)", FerramentaMoverVertice),
            ("mFerramentasSoloDesenhar", "desenhar.svg", "Desenhar com guias 90° / 45°", FerramentaDesenhar),
        ]
        for nome, icone, texto, Classe in defs:
            acao = QAction(QIcon(os.path.join(PASTA, "guias", "icons", icone)), texto,
                           self.iface.mainWindow())
            acao.setObjectName(nome)
            acao.setCheckable(True)
            ferramenta = Classe(self.iface, acao, self.medidas, self.intervalo, self.meio)
            acao.triggered.connect(lambda _c=False, f=ferramenta: self.iface.mapCanvas().setMapTool(f))
            self.toolbar.addAction(acao)
            self.iface.addPluginToVectorMenu(MENU, acao)
            self.acoes.append(acao)
            self.ferramentas.append(ferramenta)

        # Liga/desliga das medidas ao vivo (logo ao lado das ferramentas de desenho)
        acao_med = self.medidas.criar_acao(self.iface.mainWindow())
        self.toolbar.addAction(acao_med)
        self.iface.addPluginToVectorMenu(MENU, acao_med)

        # Intervalo de distância (aderência por passo): clique liga/desliga, seta escolhe o valor
        acao_int = self.intervalo.criar_acao(self.iface.mainWindow())
        self.toolbar.addAction(acao_int)
        self.intervalo.configurar_botao(self.toolbar)
        self.iface.addPluginToVectorMenu(MENU, acao_int)
        self.iface.addPluginToVectorMenu(MENU, self.intervalo.menu.menuAction())

        # Aderência no meio do lado (ponto médio de cada face)
        acao_meio = self.meio.criar_acao(self.iface.mainWindow())
        self.toolbar.addAction(acao_meio)
        self.iface.addPluginToVectorMenu(MENU, acao_meio)
        self.toolbar.addSeparator()

        # Comprimento e Área (rótulos fixos das camadas em edição)
        self.comprimento.initGui()

        # Preencher Campo, Seleção por Sobreposição, Importar/Exportar DXF, Relatório
        for modulo, classe, separar in EXTRAS:
            if separar:
                self.toolbar.addSeparator()
            try:
                Classe = getattr(importlib.import_module("." + modulo, __package__), classe)
                obj = Classe(self.iface, toolbar=self.toolbar, menu=MENU)
                obj.initGui()
                self.extras[modulo] = obj
            except Exception as exc:   # um complemento com problema não derruba os outros
                self._log("Não foi possível carregar %s: %s" % (modulo, exc))

        self._registrar_atalhos()
        QTimer.singleShot(3000, self._avisar_antigos)

    # acesso direto aos complementos extras
    @property
    def dxf(self):
        return self.extras.get("dxf.plugin")

    @property
    def relatorio(self):
        return self.extras.get("relatorio.plugin")

    # ---------- atalhos de teclado ----------
    def _registrar_atalhos(self):
        """Coloca todos os botões da barra em Configurações > Atalhos de teclado.
        Os que já têm atalho padrão (Beiral, Restaurar, Filetes) mantêm o padrão; o QGIS
        guarda a escolha do usuário pelo nome do botão (objectName)."""
        try:
            from qgis.gui import QgsGui
            gerenciador = QgsGui.shortcutsManager()
        except Exception:
            gerenciador = None
        for acao in self.toolbar.actions():
            if acao.isSeparator() or not acao.text():
                continue
            if not acao.objectName():
                acao.setObjectName("mFerramentasSolo_" + "".join(c for c in acao.text() if c.isalnum()))
            if gerenciador is not None and gerenciador.actionByName(acao.objectName()) is acao:
                continue          # o próprio módulo já registrou (Beiral, Restaurar, Preencher)
            self.iface.registerMainWindowAction(acao, acao.shortcut().toString())
            self.atalhos.append(acao)

    def _avisar_antigos(self):
        try:
            from qgis.utils import active_plugins
        except Exception:
            return
        if self.toolbar is None:
            return
        ativos = [nome for pasta, nome in ANTIGOS.items() if pasta in active_plugins]
        if ativos:
            self.iface.messageBar().pushMessage(
                TITULO,
                "O(s) complemento(s) %s agora faz(em) parte do Ferramentas Solo. Desinstale-o(s) em "
                "Complementos > Gerenciar e Instalar para não duplicar botões e atalhos."
                % ", ".join('"%s"' % n for n in ativos),
                level=NIVEL_AVISO, duration=0)
        # cópias antigas dos scripts na pasta Scripts do Processamento (aparecem duas vezes)
        if self.provedor is None:
            return
        antigos_ids = {"calcular_testadas_lotes"} | {a.name() for a in self.provedor.algorithms()}
        reg = QgsApplication.processingRegistry()
        repetidos = sorted(reg.algorithmById("script:" + n).displayName() for n in antigos_ids
                           if reg.algorithmById("script:" + n) is not None)
        if repetidos:
            self.iface.messageBar().pushMessage(
                TITULO,
                "Estes scripts agora estão em Processamento > Ferramentas Solo e também continuam "
                "em Processamento > Scripts: %s. Pode excluir as cópias antigas (botão direito > "
                "Excluir script) para não aparecerem duas vezes." % ", ".join(repetidos),
                level=NIVEL_AVISO, duration=0)

    def unload(self):
        for acao in self.atalhos:
            self.iface.unregisterMainWindowAction(acao)
        self.atalhos = []
        canvas = self.iface.mapCanvas()
        for f in self.ferramentas:
            if canvas.mapTool() is f:
                canvas.unsetMapTool(f)
            f.limpar_visual()
            f.remover_itens()
        for a in self.acoes:
            self.iface.removePluginVectorMenu(MENU, a)
            a.deleteLater()
        if self.medidas:
            if self.medidas.acao is not None:
                self.iface.removePluginVectorMenu(MENU, self.medidas.acao)
            self.medidas.remover()
        if self.intervalo:
            if self.intervalo.acao is not None:
                self.iface.removePluginVectorMenu(MENU, self.intervalo.acao)
            if self.intervalo.menu is not None:
                self.iface.removePluginVectorMenu(MENU, self.intervalo.menu.menuAction())
            self.intervalo.remover()
            self.intervalo = None
        if self.meio:
            if self.meio.acao is not None:
                self.iface.removePluginVectorMenu(MENU, self.meio.acao)
            self.meio.remover()
            self.meio = None
        for obj in reversed(list(self.extras.values())):
            try:
                obj.unload()
            except Exception as exc:
                self._log("Erro ao descarregar: %s" % exc)
        self.extras = {}
        if self.provedor is not None:
            QgsApplication.processingRegistry().removeProvider(self.provedor)
            self.provedor = None
        if self.comprimento:
            self.comprimento.unload()
        if self.filetes:
            self.filetes.unload()
        if self.beiral:
            self.beiral.unload()
        if self.toolbar:
            self.toolbar.deleteLater()
        self.acoes, self.ferramentas = [], []
        self.beiral = self.filetes = self.comprimento = self.medidas = self.toolbar = None
