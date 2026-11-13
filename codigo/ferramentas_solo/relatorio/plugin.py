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
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateTransform, QgsFeatureRequest, QgsProject, QgsSettings,
    QgsSpatialIndex, QgsVectorLayer, QgsWkbTypes,
)

from .core import SEM_USUARIO, SEM_VALOR, agregar, parse_texto_data, por_setor
from . import exportar

PASTA = os.path.dirname(__file__)
CHAVE = "relatorio_producao/"
NENHUM = "(nenhum)"
NENHUMA = "(nenhuma)"
try:
    POLIGONO = Qgis.GeometryType.Polygon          # QGIS >= 3.30
except AttributeError:
    POLIGONO = QgsWkbTypes.PolygonGeometry


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


class Representar:
    """Texto do valor como aparece no QGIS (ex.: o nome do mapa de valores), com cache."""

    def __init__(self, lyr, idx):
        self.lyr, self.idx, self.cache = lyr, idx, {}
        setup = lyr.editorWidgetSetup(idx)
        self.tipo, self.cfg = setup.type(), setup.config()
        self.fmt = QgsApplication.fieldFormatterRegistry().fieldFormatter(self.tipo)   # sem widget: o padrão
        try:
            self.fmt_cache = self.fmt.createCache(lyr, idx, self.cfg)
        except Exception:
            self.fmt_cache = None

    def __call__(self, v):
        if _nulo(v) or str(v).strip() == "":
            return None
        chave = str(v)
        if chave not in self.cache:
            try:
                txt = self.fmt.representValue(self.lyr, self.idx, self.cfg, self.fmt_cache, v)
            except Exception:
                txt = chave
            self.cache[chave] = txt if txt not in (None, "") else chave
        return self.cache[chave]

    def ordem(self):
        """Ordem das descrições do mapa de valores (para as colunas saírem na mesma ordem)."""
        if self.tipo != "ValueMap":
            return []
        mapa = self.cfg.get("map", [])
        if isinstance(mapa, dict):
            return list(mapa.keys())
        out = []
        for item in mapa:
            for desc, valor in dict(item).items():
                if not str(valor).startswith("{2839923C"):      # a entrada NULL do QGIS fica de fora
                    out.append(str(desc))
        return out


def _nulo(v):
    if v is None:
        return True
    try:
        return v.isNull()
    except AttributeError:
        return False


class Setores:
    """Nomes dos setores (polígonos) em que cada feição cai."""

    def __init__(self, lyr, campo):
        self.crs = lyr.crs()
        self.geoms = {}
        self.indice = QgsSpatialIndex()
        for ft in lyr.getFeatures():
            if ft.hasGeometry():
                nome = ft[campo]
                self.geoms[ft.id()] = ("(sem código)" if _nulo(nome) else str(nome), ft.geometry())
                self.indice.addFeature(ft)

    def __call__(self, geom, xform=None):
        if geom.isNull() or geom.isEmpty():
            return []
        # ponto na superfície: lote na divisa de dois setores conta num só (em camada de pontos é o próprio ponto)
        ponto = geom.pointOnSurface()
        if xform is not None:
            ponto.transform(xform)
        candidatos = (self.geoms[i] for i in self.indice.intersects(ponto.boundingBox()))
        return sorted({nome for nome, g in candidatos if g.intersects(ponto)})


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
        self.cb_valor = QComboBox()
        self.cb_valor.setToolTip("Opcional: conta, para cada usuário e período, quantas feições ficaram com "
                                 "cada valor deste campo (com os nomes do mapa de valores, se houver)")
        f.addRow("Campo do valor:", self.cb_valor)
        self.chk_preenchido = QCheckBox("Contar só as feições com esse campo preenchido")
        self.chk_preenchido.setChecked(self.s.value(CHAVE + "so_preenchido", True, type=bool))
        f.addRow("", self.chk_preenchido)
        self.cb_valor.currentIndexChanged.connect(
            lambda _i: self.chk_preenchido.setEnabled(self.cb_valor.currentText() != NENHUM))

        self.cb_setores = QComboBox()
        self.cb_setores.setToolTip("Opcional: cria a aba \"Por setor\", contando as feições dentro de cada polígono "
                                   "desta camada (ex.: Setores), separadas pelos valores do campo do valor")
        self.cb_campo_setor = QComboBox()
        self.cb_setores.currentIndexChanged.connect(self._atualizar_campo_setor)
        f.addRow("Camada de setores:", self.cb_setores)
        f.addRow("Campo do setor:", self.cb_campo_setor)

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
        self.tb_valor = self._tabela()             # abas do campo do valor (só aparecem quando usado)
        self.tb_valor_periodo = self._tabela()
        self.tb_setor = self._tabela()             # abas da camada de setores (só aparecem quando usada)
        self.tb_usuario_setor = self._tabela()
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
        poligonos = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer):
                continue
            if lyr.geometryType() == POLIGONO:
                poligonos.append(lyr)
            it = QListWidgetItem(lyr.name())
            it.setData(Qt.UserRole, lyr.id())
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            marcar = lyr.id() in marcadas if marcadas else (ativa is not None and lyr.id() == ativa.id())
            it.setCheckState(Qt.Checked if marcar else Qt.Unchecked)
            self.lista.addItem(it)
        self.lista.sortItems()
        self.lista.blockSignals(False)
        self._atualizar_campos()

        atual = self.cb_setores.currentData() or self.s.value(CHAVE + "camada_setores", "")
        self.cb_setores.blockSignals(True)
        self.cb_setores.clear()
        self.cb_setores.addItem(NENHUMA, "")
        for lyr in sorted(poligonos, key=lambda l: l.name().lower()):
            self.cb_setores.addItem(lyr.name(), lyr.id())
        self.cb_setores.setCurrentIndex(max(0, self.cb_setores.findData(atual)))
        self.cb_setores.blockSignals(False)
        self._atualizar_campo_setor()

    def _camada_setores(self):
        return QgsProject.instance().mapLayer(self.cb_setores.currentData() or "")

    def _atualizar_campo_setor(self, *_):
        lyr = self._camada_setores()
        self.cb_campo_setor.clear()
        self.cb_campo_setor.setEnabled(lyr is not None)
        if lyr is None:
            return
        nomes = [fld.name() for fld in lyr.fields()]
        self.cb_campo_setor.addItems(nomes)
        # padrão: o último usado; senão, o campo da simbologia categorizada da camada
        rend = lyr.renderer()
        for nome in (self.s.value(CHAVE + "campo_setor", ""),
                     rend.classAttribute() if rend is not None and rend.type() == "categorizedSymbol" else ""):
            if nome in nomes:
                self.cb_campo_setor.setCurrentText(nome)
                break

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
        atual = self.cb_valor.currentText() or self.s.value(CHAVE + "campo_valor", NENHUM)
        self.cb_valor.blockSignals(True)
        self.cb_valor.clear()
        self.cb_valor.addItem(NENHUM)
        self.cb_valor.addItems(nomes)
        self.cb_valor.setCurrentIndex(max(0, self.cb_valor.findText(atual)))
        self.cb_valor.blockSignals(False)
        self.chk_preenchido.setEnabled(self.cb_valor.currentText() != NENHUM)

    # ---------- processamento ----------
    def gerar(self):
        camadas = self._camadas_marcadas()
        if not camadas:
            QMessageBox.warning(self, "Relatório", "Marque ao menos uma camada.")
            return
        c_user = self.cb_user.currentText().strip()
        c_data = self.cb_data.currentText().strip()
        c_valor = self.cb_valor.currentText().strip()
        c_valor = "" if c_valor == NENHUM else c_valor
        campos = [c_user, c_data] + ([c_valor] if c_valor else [])
        faltando = [l.name() for l in camadas if any(l.fields().indexOf(c) < 0 for c in campos)]
        if faltando:
            QMessageBox.warning(self, "Relatório",
                                "Campo %s não existe em:\n- %s" % (" ou ".join("'%s'" % c for c in campos),
                                                                    "\n- ".join(faltando)))
            return
        so_preenchido = bool(c_valor) and self.chk_preenchido.isChecked()
        lyr_setores = self._camada_setores()
        c_setor = self.cb_campo_setor.currentText()
        if lyr_setores is not None and lyr_setores.fields().indexOf(c_setor) < 0:
            QMessageBox.warning(self, "Relatório", "Escolha o campo do setor.")
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
        self.s.setValue(CHAVE + "campo_valor", c_valor or NENHUM)
        self.s.setValue(CHAVE + "so_preenchido", self.chk_preenchido.isChecked())
        self.s.setValue(CHAVE + "camada_setores", lyr_setores.id() if lyr_setores is not None else "")
        if lyr_setores is not None:
            self.s.setValue(CHAVE + "campo_setor", c_setor)

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            registros = []
            fora = 0
            sem_valor = 0
            ordem = []
            setores = Setores(lyr_setores, c_setor) if lyr_setores is not None else None
            pares = []          # (setores em que a feição cai, valor) das feições contadas
            em_varios = 0
            for lyr in camadas:
                req = QgsFeatureRequest()
                xform = None
                if setores is None:
                    try:
                        req.setFlags(QgsFeatureRequest.NoGeometry)
                    except Exception:
                        pass
                elif lyr.crs() != setores.crs:
                    xform = QgsCoordinateTransform(lyr.crs(), setores.crs, QgsProject.instance())
                req.setSubsetOfAttributes(campos, lyr.fields())
                it = lyr.getSelectedFeatures(req) if so_sel else lyr.getFeatures(req)
                i_u, i_d = lyr.fields().indexOf(c_user), lyr.fields().indexOf(c_data)
                rep = None
                if c_valor:
                    i_v = lyr.fields().indexOf(c_valor)
                    rep = Representar(lyr, i_v)
                    ordem += [o for o in rep.ordem() if o not in ordem]
                for ft in it:
                    d = para_data(ft.attribute(i_d))
                    if filtrar and (d is None or d < d_ini or d > d_fim):
                        fora += 1
                        continue
                    valor = None
                    if rep is not None:
                        valor = rep(ft.attribute(i_v))
                        if valor is None and so_preenchido:
                            sem_valor += 1
                            continue
                    user = para_usuario(ft.attribute(i_u))
                    reg = (lyr.name(), user, d)
                    registros.append(reg if rep is None else reg + (valor,))
                    if setores is not None:
                        nomes = setores(ft.geometry(), xform)
                        em_varios += len(nomes) > 1
                        pares.append((nomes, valor, (user or "").strip() or SEM_USUARIO))   # usuário como no agregar
            self.res = agregar(registros, modo, [l.name() for l in camadas], ordem)
            if setores is not None:
                # o percentual é do primeiro valor do mapa de valores (sem mapa, não há percentual)
                self.res["setor"] = por_setor([(s, v) for s, v, _u in pares], self.res.get("valores") or [],
                                              ordem[0] if ordem else None)
                self.res["usuario_setor"] = por_setor([(s, u) for s, _v, u in pares], self.res["usuarios"])
        finally:
            QApplication.restoreOverrideCursor()

        self._preencher()
        r = self.res
        self.info = [
            "Camadas: " + ", ".join(r["camadas"]),
            "Campos: usuário = %s | data = %s" % (c_user, c_data),
            "Período: " + ("%s a %s" % (d_ini.strftime("%d/%m/%Y"), d_fim.strftime("%d/%m/%Y")) if filtrar else "todos"),
            "Agrupamento: " + self.cb_agrup.currentText() + (" | apenas selecionadas" if so_sel else ""),
        ] + ([
            "Campo do valor: %s%s" % (c_valor, " (só feições com o campo preenchido)" if so_preenchido else ""),
        ] if c_valor else []) + ([
            "Setores: %s (campo %s)" % (lyr_setores.name(), c_setor),
        ] if lyr_setores is not None else []) + [
            "Gerado em: " + dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        ]
        txt = ("<b>Total produzido:</b> %d &nbsp;|&nbsp; <b>Usuários:</b> %d &nbsp;|&nbsp; "
               "<b>Dias com produção:</b> %d &nbsp;|&nbsp; <b>Média geral/dia:</b> %.1f"
               % (r["total"], len(r["usuarios"]), r["dias_com_producao"], r["media_geral_dia"]))
        if r["sem_data"]:
            txt += " &nbsp;|&nbsp; <span style='color:#b00'>%d sem data</span>" % r["sem_data"]
        if filtrar and fora:
            txt += " &nbsp;|&nbsp; %d fora do período" % fora
        if c_valor and so_preenchido and sem_valor:
            txt += " &nbsp;|&nbsp; %d sem '%s' preenchido (não contadas)" % (sem_valor, c_valor)
        if em_varios:
            txt += (" &nbsp;|&nbsp; <span style='color:#b00'>%d em mais de um setor (contadas em cada um)</span>"
                    % em_varios)
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
                if j > 0 and isinstance(v, (int, float)):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if i == len(linhas) - 1:
                    it.setFont(fonte_neg)
                tabela.setItem(i, j, it)
        tabela.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _preencher(self):
        r = self.res
        self._encher(self.tb_resumo, exportar.cabecalho_resumo(r), exportar.linhas_resumo(r))
        self._encher(self.tb_periodo, exportar.cabecalho_periodo(r), exportar.linhas_periodo(r))
        com_valor = r.get("valores") is not None
        for tabela, nome, mostrar, cab, linhas in (
                (self.tb_valor, "Por valor", com_valor, exportar.cabecalho_valor, exportar.linhas_valor),
                (self.tb_valor_periodo, "Valor por período", com_valor, exportar.cabecalho_valor_periodo,
                 exportar.linhas_valor_periodo),
                (self.tb_setor, "Por setor", "setor" in r, exportar.cabecalho_setor, exportar.linhas_setor),
                (self.tb_usuario_setor, "Usuário por setor", "setor" in r, exportar.cabecalho_usuario_setor,
                 exportar.linhas_usuario_setor)):
            i = self.abas.indexOf(tabela)
            if mostrar:
                self._encher(tabela, cab(r), linhas(r))
                if i < 0:
                    self.abas.addTab(tabela, nome)
            elif i >= 0:
                self.abas.removeTab(i)

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
