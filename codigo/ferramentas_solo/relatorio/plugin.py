# -*- coding: utf-8 -*-
import datetime as dt
import os

from qgis.PyQt.QtCore import Qt, QDate, QDateTime
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QListWidget,
    QListWidgetItem, QComboBox, QCheckBox, QDateEdit, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QLabel, QFileDialog, QMessageBox, QHeaderView,
    QApplication, QAbstractItemView,
)
from qgis.core import QgsProject, QgsVectorLayer, QgsFeatureRequest, QgsSettings

from .core import agregar, parse_texto_data
from . import exportar

PASTA = os.path.dirname(__file__)
CHAVE = "relatorio_producao/"


def para_data(v):
    """Converte valor do atributo (QDateTime, QDate, datetime, texto) em datetime.date."""
    if v is None:
        return None
    if isinstance(v, QDateTime):
        if not v.isValid():
            return None
        if v.timeSpec() == Qt.UTC:
            v = v.toLocalTime()
        return v.date().toPyDate()
    if isinstance(v, QDate):
        return v.toPyDate() if v.isValid() else None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        if v.isNull():          # QVariant NULL
            return None
    except AttributeError:
        pass
    return parse_texto_data(v)


def para_usuario(v):
    if v is None:
        return None
    try:
        if v.isNull():
            return None
    except AttributeError:
        pass
    return str(v)


class DialogoRelatorio(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.res = None
        self.info = []
        self.s = QgsSettings()
        self.setWindowTitle("Relatório de Produtividade")
        self.resize(900, 640)
        self._montar()
        self._carregar_camadas()

    # ---------- interface ----------
    def _montar(self):
        lay = QVBoxLayout(self)
        topo = QHBoxLayout()

        g1 = QGroupBox("Camadas")
        v1 = QVBoxLayout(g1)
        self.lista = QListWidget()
        self.lista.itemChanged.connect(self._atualizar_campos)
        v1.addWidget(self.lista)
        self.chk_sel = QCheckBox("Apenas feições selecionadas")
        v1.addWidget(self.chk_sel)
        topo.addWidget(g1, 1)

        g2 = QGroupBox("Parâmetros")
        f = QFormLayout(g2)
        self.cb_user = QComboBox(); self.cb_user.setEditable(True)
        self.cb_data = QComboBox(); self.cb_data.setEditable(True)
        f.addRow("Campo usuário:", self.cb_user)
        f.addRow("Campo data:", self.cb_data)

        self.chk_periodo = QCheckBox("Filtrar período")
        self.chk_periodo.setChecked(self.s.value(CHAVE + "filtrar", False, type=bool))
        hoje = QDate.currentDate()
        self.de = QDateEdit(QDate(hoje.year(), hoje.month(), 1)); self.de.setCalendarPopup(True)
        self.ate = QDateEdit(hoje); self.ate.setCalendarPopup(True)
        for w in (self.de, self.ate):
            w.setDisplayFormat("dd/MM/yyyy")
        hp = QHBoxLayout(); hp.addWidget(self.de); hp.addWidget(QLabel("até")); hp.addWidget(self.ate)
        f.addRow(self.chk_periodo, hp)
        self.chk_periodo.toggled.connect(lambda on: (self.de.setEnabled(on), self.ate.setEnabled(on)))
        self.de.setEnabled(self.chk_periodo.isChecked()); self.ate.setEnabled(self.chk_periodo.isChecked())

        self.cb_agrup = QComboBox()
        self.cb_agrup.addItem("Diário", "dia")
        self.cb_agrup.addItem("Semanal", "semana")
        self.cb_agrup.addItem("Mensal", "mes")
        self.cb_agrup.setCurrentIndex(max(0, self.cb_agrup.findData(self.s.value(CHAVE + "agrup", "dia"))))
        f.addRow("Agrupar por:", self.cb_agrup)

        self.bt_gerar = QPushButton("Gerar relatório")
        self.bt_gerar.clicked.connect(self.gerar)
        f.addRow(self.bt_gerar)
        topo.addWidget(g2, 1)
        lay.addLayout(topo)

        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)

        self.abas = QTabWidget()
        self.tb_resumo = self._tabela()
        self.tb_periodo = self._tabela()
        self.abas.addTab(self.tb_resumo, "Resumo por usuário")
        self.abas.addTab(self.tb_periodo, "Por período")
        lay.addWidget(self.abas, 1)

        rod = QHBoxLayout()
        self.bt_copiar = QPushButton("Copiar tabela")
        self.bt_copiar.clicked.connect(self.copiar)
        self.bt_exportar = QPushButton("Exportar (Excel/CSV)…")
        self.bt_exportar.clicked.connect(self.exportar)
        for b in (self.bt_copiar, self.bt_exportar):
            b.setEnabled(False)
        fechar = QPushButton("Fechar"); fechar.clicked.connect(self.close)
        rod.addWidget(self.bt_copiar); rod.addWidget(self.bt_exportar); rod.addStretch(); rod.addWidget(fechar)
        lay.addLayout(rod)

    def _tabela(self):
        t = QTableWidget()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSortingEnabled(False)
        t.setAlternatingRowColors(True)
        return t

    def _carregar_camadas(self):
        marcadas = self.s.value(CHAVE + "camadas", [], type=list) or []
        ativa = self.iface.activeLayer()
        self.lista.blockSignals(True)
        self.lista.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            it = QListWidgetItem(lyr.name())
            it.setData(Qt.UserRole, lyr.id())
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            marcar = lyr.id() in marcadas if marcadas else (ativa is not None and lyr.id() == ativa.id())
            it.setCheckState(Qt.Checked if marcar else Qt.Unchecked)
            self.lista.addItem(it)
        self.lista.sortItems()
        self.lista.blockSignals(False)
        self._atualizar_campos()

    def _camadas_marcadas(self):
        out = []
        for i in range(self.lista.count()):
            it = self.lista.item(i)
            if it.checkState() == Qt.Checked:
                lyr = QgsProject.instance().mapLayer(it.data(Qt.UserRole))
                if lyr:
                    out.append(lyr)
        return out

    def _atualizar_campos(self, *_):
        nomes = []
        for lyr in self._camadas_marcadas():
            for fld in lyr.fields():
                if fld.name() not in nomes:
                    nomes.append(fld.name())
        for cb, chave, padrao in ((self.cb_user, "campo_user", "user"),
                                  (self.cb_data, "campo_data", "created_at")):
            atual = cb.currentText() or self.s.value(CHAVE + chave, padrao)
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(nomes)
            idx = cb.findText(atual)
            if idx < 0:
                idx = cb.findText(padrao)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            else:
                cb.setEditText(atual)
            cb.blockSignals(False)

    # ---------- processamento ----------
    def gerar(self):
        camadas = self._camadas_marcadas()
        if not camadas:
            QMessageBox.warning(self, "Relatório", "Marque ao menos uma camada.")
            return
        c_user = self.cb_user.currentText().strip()
        c_data = self.cb_data.currentText().strip()
        faltando = [l.name() for l in camadas
                    if l.fields().indexOf(c_user) < 0 or l.fields().indexOf(c_data) < 0]
        if faltando:
            QMessageBox.warning(self, "Relatório",
                                "Campo '%s' ou '%s' não existe em:\n- %s" % (c_user, c_data, "\n- ".join(faltando)))
            return

        filtrar = self.chk_periodo.isChecked()
        d_ini, d_fim = self.de.date().toPyDate(), self.ate.date().toPyDate()
        if filtrar and d_ini > d_fim:
            QMessageBox.warning(self, "Relatório", "Data inicial maior que a final.")
            return
        so_sel = self.chk_sel.isChecked()
        modo = self.cb_agrup.currentData()

        # guarda preferências
        self.s.setValue(CHAVE + "camadas", [l.id() for l in camadas])
        self.s.setValue(CHAVE + "campo_user", c_user)
        self.s.setValue(CHAVE + "campo_data", c_data)
        self.s.setValue(CHAVE + "filtrar", filtrar)
        self.s.setValue(CHAVE + "agrup", modo)

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            registros = []
            fora = 0
            for lyr in camadas:
                req = QgsFeatureRequest()
                try:
                    req.setFlags(QgsFeatureRequest.NoGeometry)
                except Exception:
                    pass
                req.setSubsetOfAttributes([c_user, c_data], lyr.fields())
                it = lyr.getSelectedFeatures(req) if so_sel else lyr.getFeatures(req)
                i_u, i_d = lyr.fields().indexOf(c_user), lyr.fields().indexOf(c_data)
                for ft in it:
                    d = para_data(ft.attribute(i_d))
                    if filtrar and (d is None or d < d_ini or d > d_fim):
                        fora += 1
                        continue
                    registros.append((lyr.name(), para_usuario(ft.attribute(i_u)), d))
            self.res = agregar(registros, modo, [l.name() for l in camadas])
        finally:
            QApplication.restoreOverrideCursor()

        self._preencher()
        r = self.res
        self.info = [
            "Camadas: " + ", ".join(r["camadas"]),
            "Campos: usuário = %s | data = %s" % (c_user, c_data),
            "Período: " + ("%s a %s" % (d_ini.strftime("%d/%m/%Y"), d_fim.strftime("%d/%m/%Y")) if filtrar else "todos"),
            "Agrupamento: " + self.cb_agrup.currentText() + (" | apenas selecionadas" if so_sel else ""),
            "Gerado em: " + dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        ]
        txt = ("<b>Total produzido:</b> %d &nbsp;|&nbsp; <b>Usuários:</b> %d &nbsp;|&nbsp; "
               "<b>Dias com produção:</b> %d &nbsp;|&nbsp; <b>Média geral/dia:</b> %.1f"
               % (r["total"], len(r["usuarios"]), r["dias_com_producao"], r["media_geral_dia"]))
        if r["sem_data"]:
            txt += " &nbsp;|&nbsp; <span style='color:#b00'>%d sem data</span>" % r["sem_data"]
        if filtrar and fora:
            txt += " &nbsp;|&nbsp; %d fora do período" % fora
        self.lbl.setText(txt)
        for b in (self.bt_copiar, self.bt_exportar):
            b.setEnabled(r["total"] > 0)

    def _encher(self, tabela, cab, linhas):
        tabela.clear()
        tabela.setColumnCount(len(cab))
        tabela.setRowCount(len(linhas))
        tabela.setHorizontalHeaderLabels([str(c) for c in cab])
        fonte_neg = tabela.font(); fonte_neg.setBold(True)
        for i, lin in enumerate(linhas):
            for j, v in enumerate(lin):
                txt = ("%.1f" % v).replace(".", ",") if isinstance(v, float) else str(v)
                it = QTableWidgetItem(txt)
                if j > 0:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if i == len(linhas) - 1:
                    it.setFont(fonte_neg)
                tabela.setItem(i, j, it)
        tabela.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _preencher(self):
        r = self.res
        self._encher(self.tb_resumo, exportar.cabecalho_resumo(r), exportar.linhas_resumo(r))
        self._encher(self.tb_periodo, exportar.cabecalho_periodo(r), exportar.linhas_periodo(r))

    # ---------- saída ----------
    def copiar(self):
        t = self.abas.currentWidget()
        linhas = ["\t".join(t.horizontalHeaderItem(c).text() for c in range(t.columnCount()))]
        for r in range(t.rowCount()):
            linhas.append("\t".join(t.item(r, c).text() if t.item(r, c) else "" for c in range(t.columnCount())))
        QApplication.clipboard().setText("\n".join(linhas))
        self.iface.messageBar().pushInfo("Relatório", "Tabela copiada — cole no Excel.")

    def exportar(self):
        if not self.res:
            return
        pasta = self.s.value(CHAVE + "pasta", os.path.expanduser("~"))
        nome = "producao_%s.xlsx" % dt.datetime.now().strftime("%Y%m%d_%H%M")
        caminho, filtro = QFileDialog.getSaveFileName(
            self, "Exportar relatório", os.path.join(pasta, nome),
            "Excel (*.xlsx);;CSV (*.csv)")
        if not caminho:
            return
        self.s.setValue(CHAVE + "pasta", os.path.dirname(caminho))
        try:
            if caminho.lower().endswith(".csv") or filtro.startswith("CSV"):
                arqs = exportar.exportar_csv(caminho, self.res, self.info)
            else:
                if not caminho.lower().endswith(".xlsx"):
                    caminho += ".xlsx"
                try:
                    arqs = exportar.exportar_xlsx(caminho, self.res, self.info)
                except ImportError:
                    arqs = exportar.exportar_csv(caminho, self.res, self.info)
                    QMessageBox.information(self, "Relatório",
                                            "openpyxl não encontrado no QGIS — exportado em CSV.")
        except PermissionError:
            QMessageBox.critical(self, "Relatório", "Sem permissão para gravar. O arquivo está aberto no Excel?")
            return
        self.iface.messageBar().pushSuccess("Relatório", "Exportado: " + " | ".join(arqs))
        try:
            os.startfile(arqs[0])
        except Exception:
            pass


class RelatorioProducaoPlugin:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar          # barra do Ferramentas Solo
        self.menu = menu or "&Relatório de Produtividade"
        self.acao = None
        self.dlg = None

    def initGui(self):
        self.acao = QAction(QIcon(os.path.join(PASTA, "icon.svg")), "Relatório de Produtividade",
                            self.iface.mainWindow())
        self.acao.setObjectName("mFerramentasSoloRelatorio")
        self.acao.setToolTip("Relatório de produtividade por usuário e data")
        self.acao.triggered.connect(self.abrir)
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
        if self.dlg:
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None

    def abrir(self):
        if self.dlg is None:
            self.dlg = DialogoRelatorio(self.iface)
        else:
            self.dlg._carregar_camadas()
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
