# -*- coding: utf-8 -*-
"""Backup de Camadas
Grava todas as camadas vetoriais do projeto num GeoPackage (com estilos e metadados) em
<pasta do projeto>/Backup_camadas/<projeto>_<data>_<hora>.gpkg, em segundo plano.
Não mexe nas camadas: edições não salvas ficam de fora do backup (e a ferramenta avisa quais).
Grava camada por camada: se uma falhar, as outras ficam no backup e a ferramenta avisa qual ficou de fora."""
import os
from datetime import datetime

from qgis.PyQt.QtGui import QIcon
from qgis.core import (Qgis, QgsApplication, QgsField, QgsGeometry, QgsMapLayerStyle, QgsMessageLog, QgsProject,
                       QgsProviderRegistry, QgsTask, QgsVariantUtils, QgsVectorFileWriter, QgsVectorLayer)
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

try:
    CRITICO = Qgis.MessageLevel.Critical
except AttributeError:
    CRITICO = Qgis.Critical
ACAO = getattr(QgsVectorFileWriter, "ActionOnExistingFile", QgsVectorFileWriter)
SEM_ERRO = getattr(QgsVectorFileWriter, "WriterError", QgsVectorFileWriter).NoError

TITULO = "Backup de Camadas"
PASTA = os.path.dirname(__file__)
PASTA_BACKUP = "Backup_camadas"


def caminho_backup(pasta_projeto, nome_projeto, agora):
    # sem ":" no nome, que o Windows não aceita
    return os.path.join(pasta_projeto, PASTA_BACKUP, f"{nome_projeto}_{agora:%Y-%m-%d_%H-%M-%S}.gpkg")


def nome_unico(nome, vistos):
    """O GeoPackage não diferencia maiúsculas: "Lotes" e "lotes" (ou campos "Area" e "area") colidem."""
    novo, n = nome, 1
    while novo.lower() in vistos:
        n += 1
        novo = f"{nome}_{n}"
    vistos.add(novo.lower())
    return novo


class Renomear(QgsVectorFileWriter.FieldValueConverter):
    """Renomeia os campos que colidem no GeoPackage (area -> area_2)."""

    def __init__(self, campos):
        super().__init__()
        self.vistos = set()
        self.nomes = {c.name(): nome_unico(c.name(), self.vistos) for c in campos}

    def fieldDefinition(self, campo):
        novo = QgsField(campo)
        novo.setName(self.nomes.get(campo.name(), campo.name()))
        return novo


def tipo_proprio(campo):
    # tipo do próprio QGIS (QMetaType.User em diante), ex.: coluna geometry de tabela aberta sem geometria
    return campo.type() >= 1024


def texto(nome):
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(nome, QMetaType.Type.QString)          # QGIS >= 3.38 / Qt6
    except (ImportError, AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(nome, QVariant.String)


class RenomearETexto(Renomear):
    """Também grava como texto (geometria em WKT) os campos de tipo que o GeoPackage não aceita.
    Classe à parte: o convert em Python roda para cada valor, só vale pagar isso quando precisa."""

    def __init__(self, campos):
        super().__init__(campos)
        self.textos = {i for i, c in enumerate(campos) if tipo_proprio(c)}

    def fieldDefinition(self, campo):
        novo = super().fieldDefinition(campo)
        return texto(novo.name()) if tipo_proprio(campo) else novo

    def convert(self, i, valor):
        if i not in self.textos or valor is None or QgsVariantUtils.isNull(valor):
            return valor
        return valor.asWkt() if isinstance(valor, QgsGeometry) else str(valor)


def conversor(campos):
    return (RenomearETexto if any(tipo_proprio(c) for c in campos) else Renomear)(campos)


def gravar(camada, destino, tabela, conv, contexto):
    op = QgsVectorFileWriter.SaveVectorOptions()
    op.driverName = "GPKG"
    op.layerName = tabela
    op.fieldValueConverter = conv
    op.actionOnExistingFile = ACAO.CreateOrOverwriteLayer if os.path.exists(destino) else ACAO.CreateOrOverwriteFile
    erro, msg = QgsVectorFileWriter.writeAsVectorFormatV3(camada, destino, contexto, op)[:2]
    return None if erro == SEM_ERRO else (msg or str(erro))


def gravar_camadas(task, trabalhos, destino, contexto):
    """task: a QgsTask (ou None). trabalhos: [(camada, tabela, conversor)].
    Devolve [(camada, erro)] das que ficaram de fora."""
    falhas = []
    for n, (camada, tabela, conv) in enumerate(trabalhos):
        if task is not None:
            task.setProgress(100.0 * n / len(trabalhos))
            if task.isCanceled():
                falhas.append((camada, "cancelado"))
                continue
        erro = gravar(camada, destino, tabela, conv, contexto)
        i_fid = camada.fields().lookupField("fid")
        if erro and i_fid >= 0:
            # "fid" com valores repetidos (tabela PostGIS que veio de GeoPackage): o GeoPackage usa esse nome
            # como chave única; regrava com o campo renomeado, mantendo os valores
            conv.nomes[camada.fields().at(i_fid).name()] = nome_unico("fid_original", conv.vistos)
            erro = gravar(camada, destino, tabela, conv, contexto)
        if erro:
            falhas.append((camada, erro))
    return falhas


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

        # shortcut: só camadas vetoriais (rasters ficam de fora); incluir se precisar
        camadas = [c for c in projeto.mapLayers().values() if isinstance(c, QgsVectorLayer) and c.isValid()]
        if not camadas:
            bar.pushWarning(TITULO, "Nenhuma camada vetorial válida no projeto.")
            return

        destino = caminho_backup(projeto.absolutePath(), projeto.baseName(), datetime.now())
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        nao_salvas = [c.name() for c in camadas if c.isModified()]
        vistos = {"layer_styles"}       # tabela dos estilos no GeoPackage
        # cópia de cada camada (sem as edições pendentes) para ler em segundo plano, como o Processamento faz;
        # os conversores ficam vivos em self.trabalhos enquanto grava (sem a referência o QGIS fecha)
        self.trabalhos = [(c.clone(), nome_unico(c.name(), vistos), conversor(c.fields())) for c in camadas]
        self.originais = camadas
        contexto = projeto.transformContext()

        self.task = QgsTask.fromFunction(TITULO, gravar_camadas, self.trabalhos, destino, contexto,
                                         # sem falhas (lista vazia) o QGIS chama on_finished só com exc
                                         on_finished=lambda exc, res=(): self.terminou(exc, res, destino, nao_salvas))
        QgsApplication.taskManager().addTask(self.task)
        bar.pushInfo(TITULO, f"Backup de {len(camadas)} camadas iniciado (acompanhe na barra de tarefas).")

    def terminou(self, exc, falhas, destino, nao_salvas):
        self.task = None
        trabalhos, originais = self.trabalhos, self.originais
        self.trabalhos = self.originais = None
        bar = self.iface.messageBar()
        if exc is not None:
            QgsMessageLog.logMessage(str(exc), TITULO, CRITICO)
            bar.pushCritical(TITULO, f"Falha no backup. Detalhes em Registro de mensagens > {TITULO}.")
            return
        fora = {id(c) for c, _e in falhas}
        # estilo e metadados depois, na thread principal
        for orig, (copia, tabela, _conv) in zip(originais, trabalhos):
            if id(copia) in fora:
                continue
            uri = f"{destino}|layername={tabela}"
            nova = QgsVectorLayer(uri, tabela, "ogr")
            estilo = QgsMapLayerStyle()
            estilo.readFromLayer(orig)
            estilo.writeToLayer(nova)
            nova.saveStyleToDatabase(tabela, "", True, "")
            QgsProviderRegistry.instance().saveLayerMetadata("ogr", uri, orig.metadata())
        for copia, erro in falhas:
            QgsMessageLog.logMessage(f"{copia.name()}: {erro}", TITULO, CRITICO)
        avisos = []
        if falhas:
            avisos.append("ficaram de fora (detalhes em Registro de mensagens > %s): %s"
                          % (TITULO, ", ".join(c.name() for c, _e in falhas)))
        if nao_salvas:
            avisos.append("entraram sem as edições não salvas: " + ", ".join(nao_salvas))
        if avisos:
            bar.pushWarning(TITULO, f"Backup salvo em {destino}, mas " + "; ".join(avisos))
        else:
            bar.pushSuccess(TITULO, f"Backup salvo em {destino}")
