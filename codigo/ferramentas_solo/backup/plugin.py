# -*- coding: utf-8 -*-
"""Backup de Camadas
Gera um pacote de camadas (GeoPackage, com estilos e metadados) de todas as camadas vetoriais
do projeto em <pasta do projeto>/Backup_camadas/<projeto>_<data>_<hora>.gpkg, em segundo plano.
Não mexe nas camadas: edições não salvas ficam de fora do backup (e a ferramenta avisa quais)."""
import os
from datetime import datetime

from qgis.PyQt.QtGui import QIcon
from qgis.core import (Qgis, QgsApplication, QgsMessageLog, QgsProcessingAlgRunnerTask,
                       QgsProcessingContext, QgsProcessingFeedback, QgsProject, QgsVectorLayer)
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

try:
    CRITICO = Qgis.MessageLevel.Critical
except AttributeError:
    CRITICO = Qgis.Critical

TITULO = "Backup de Camadas"
PASTA = os.path.dirname(__file__)
PASTA_BACKUP = "Backup_camadas"


def caminho_backup(pasta_projeto, nome_projeto, agora):
    # sem ":" no nome, que o Windows não aceita
    return os.path.join(pasta_projeto, PASTA_BACKUP, f"{nome_projeto}_{agora:%Y-%m-%d_%H-%M-%S}.gpkg")


class BackupCamadas:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar          # barra do Ferramentas Solo
        self.menu = menu or "&" + TITULO
        self.acao = None
        self.task = None

    def initGui(self):
        self.acao = QAction(QIcon(os.path.join(PASTA, "icon.svg")), TITULO, self.iface.mainWindow())
        self.acao.setObjectName("mFerramentasSoloBackupCamadas")
        self.acao.setToolTip("Backup das camadas vetoriais do projeto em GeoPackage "
                             "(pasta Backup_camadas do projeto)")
        self.acao.triggered.connect(self.run)
        if self.toolbar is not None:
            self.toolbar.addAction(self.acao)
        else:
            self.iface.addToolBarIcon(self.acao)
        self.iface.addPluginToVectorMenu(self.menu, self.acao)

    def unload(self):
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

    def run(self):
        bar = self.iface.messageBar()
        if self.task is not None:
            bar.pushWarning(TITULO, "Já existe um backup em andamento.")
            return

        projeto = QgsProject.instance()
        if not projeto.fileName() or not projeto.absolutePath():
            bar.pushWarning(TITULO, "Salve o projeto em disco antes de fazer o backup.")
            return

        # shortcut: só camadas vetoriais (rasters ficam de fora); incluir se o Pacote de camadas passar a aceitá-los
        camadas = [c for c in projeto.mapLayers().values() if isinstance(c, QgsVectorLayer) and c.isValid()]
        if not camadas:
            bar.pushWarning(TITULO, "Nenhuma camada vetorial válida no projeto.")
            return

        destino = caminho_backup(projeto.absolutePath(), projeto.baseName(), datetime.now())
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        nao_salvas = [c.name() for c in camadas if c.isModified()]

        alg = QgsApplication.processingRegistry().createAlgorithmById("native:package")
        params = {
            "LAYERS": [c.id() for c in camadas],
            "OUTPUT": destino,
            "SAVE_STYLES": True,
            "SAVE_METADATA": True,
        }
        self.context = QgsProcessingContext()
        self.context.setProject(projeto)
        self.feedback = QgsProcessingFeedback()
        self.task = QgsProcessingAlgRunnerTask(alg, params, self.context, self.feedback)
        self.task.executed.connect(lambda ok, _res: self.terminou(ok, destino, nao_salvas))
        QgsApplication.taskManager().addTask(self.task)
        bar.pushInfo(TITULO, f"Backup de {len(camadas)} camadas iniciado (acompanhe na barra de tarefas).")

    def terminou(self, ok, destino, nao_salvas):
        self.task = None
        bar = self.iface.messageBar()
        if not ok:
            QgsMessageLog.logMessage(self.feedback.textLog(), TITULO, CRITICO)
            bar.pushCritical(TITULO, f"Falha no backup. Detalhes em Registro de mensagens > {TITULO}.")
            return
        if nao_salvas:
            bar.pushWarning(TITULO, f"Backup salvo em {destino}, mas estas camadas tinham edições não salvas "
                                    f"e entraram sem elas: {', '.join(nao_salvas)}")
        else:
            bar.pushSuccess(TITULO, f"Backup salvo em {destino}")
