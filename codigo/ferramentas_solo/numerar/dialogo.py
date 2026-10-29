# -*- coding: utf-8 -*-
"""Janela do plugin Numerar Lotes e ferramenta de clique do ponto inicial."""

import math

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSettings,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox, QgsMapTool, QgsRubberBand, QgsVertexMarker

from . import core

TITULO = "Numerar Lotes"
NOME_CAMADA_QUADRAS = "Quadras geradas (Numerar Lotes)"
PREFIXO_CFG = "numerar_lotes/"

CANTOS_ROTULO = [
    ("NO", "Canto noroeste (superior esquerdo)"),
    ("NE", "Canto nordeste (superior direito)"),
    ("SE", "Canto sudeste (inferior direito)"),
    ("SO", "Canto sudoeste (inferior esquerdo)"),
]
PONTOS_TESTADA = [
    (core.FIM, "Fim da testada (como na LISP)"),
    (core.MEIO, "Meio da testada"),
    (core.INICIO, "Início da testada"),
]


# --------------------------------------------------------------------------
# Compatibilidade entre versões
# --------------------------------------------------------------------------
def _filtro_poligono():
    try:
        return Qgis.LayerFilter.PolygonLayer
    except AttributeError:
        return QgsMapLayerProxyModel.PolygonLayer


def _tipo_geom(nome):
    try:
        return getattr(Qgis.GeometryType, nome)
    except AttributeError:
        return getattr(QgsWkbTypes, nome + "Geometry")


def _icone_marcador(nome):
    tipo = getattr(QgsVertexMarker, "IconType", None)
    if tipo is not None and hasattr(tipo, nome):
        return getattr(tipo, nome)
    return getattr(QgsVertexMarker, nome)


def _campo(nome, tipo):
    """QgsField inteiro ('int') ou decimal ('double') em qualquer versão."""
    try:
        from qgis.PyQt.QtCore import QMetaType
        t = QMetaType.Type.Int if tipo == "int" else QMetaType.Type.Double
        return QgsField(nome, t)
    except (TypeError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(nome, QVariant.Int if tipo == "int" else QVariant.Double)


def _posicionamento_centro():
    try:
        return Qgis.LabelPlacement.OverPoint
    except AttributeError:
        return QgsPalLayerSettings.OverPoint


# --------------------------------------------------------------------------
# Ferramenta de mapa: clique = ponto inicial; botão direito = desfaz; Esc = sai
# --------------------------------------------------------------------------
class FerramentaPontoInicial(QgsMapTool):
    def __init__(self, canvas, dialogo):
        super().__init__(canvas)
        self.dialogo = dialogo
        self.setCursor(Qt.CursorShape.CrossCursor)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.dialogo.desfazer_ponto()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        ponto = QgsPointXY(e.mapPoint())
        try:
            encaixe = self.canvas().snappingUtils().snapToMap(ponto)
            if encaixe.isValid():
                ponto = QgsPointXY(encaixe.point())
        except Exception:
            pass
        tolerancia = 12 * self.canvas().mapUnitsPerPixel()
        self.dialogo.adicionar_ponto(ponto, tolerancia)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.dialogo.sair_ferramenta()

    def deactivate(self):
        super().deactivate()
        self.dialogo.ferramenta_desativada()


# --------------------------------------------------------------------------
# Janela
# --------------------------------------------------------------------------
class DialogoNumerarLotes(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.cfg = QgsSettings()
        self.pontos = []            # [(QgsPointXY na SRC do mapa, tolerância, SRC)]
        self.marcadores = []        # marcadores dos cliques
        self.marcadores_inicio = []
        self.ferramenta = FerramentaPontoInicial(self.canvas, self)
        self.ferramenta_anterior = None
        self._ctx = None            # última prévia (só para redesenhar)
        self._sem_numero = []
        self._id_camada_quadras = None

        self.rb_quadras = QgsRubberBand(self.canvas, _tipo_geom("Polygon"))
        self.rb_quadras.setFillColor(QColor(0, 0, 0, 0))
        self.rb_quadras.setStrokeColor(QColor(30, 120, 255))
        self.rb_quadras.setWidth(2)
        self.rb_setas = QgsRubberBand(self.canvas, _tipo_geom("Line"))
        self.rb_setas.setStrokeColor(QColor(220, 0, 0))
        self.rb_setas.setWidth(3)
        self.rb_pendentes = QgsRubberBand(self.canvas, _tipo_geom("Polygon"))
        self.rb_pendentes.setFillColor(QColor(255, 140, 0, 110))
        self.rb_pendentes.setStrokeColor(QColor(255, 110, 0))
        self.rb_pendentes.setWidth(2)

        self.setWindowTitle(TITULO)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)
        self._montar_interface()
        self._carregar_config()
        self._atualizar_estado()

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------
    def _montar_interface(self):
        geral = QVBoxLayout(self)

        # --- Lotes ------------------------------------------------------
        g_lotes = QGroupBox("Lotes")
        gl = QGridLayout(g_lotes)
        gl.addWidget(QLabel("Camada:"), 0, 0)
        self.cmb_lotes = QgsMapLayerComboBox()
        self.cmb_lotes.setFilters(_filtro_poligono())
        gl.addWidget(self.cmb_lotes, 0, 1)
        self.chk_selecionados = QCheckBox("Somente lotes selecionados")
        gl.addWidget(self.chk_selecionados, 1, 0, 1, 2)
        gl.addWidget(QLabel("Campo do número:"), 2, 0)
        self.cmb_campo = QComboBox()
        self.cmb_campo.setEditable(True)
        self.cmb_campo.setToolTip("Escolha um campo ou digite um nome novo (é criado como inteiro).")
        gl.addWidget(self.cmb_campo, 2, 1)
        geral.addWidget(g_lotes)

        # --- Quadra -----------------------------------------------------
        g_quadra = QGroupBox("Quadra")
        gq = QGridLayout(g_quadra)
        self.rb_gerar = QRadioButton("Gerar a quadra juntando os lotes")
        self.rb_existente = QRadioButton("Usar camada de quadras existente")
        grupo_q = QButtonGroup(self)
        grupo_q.addButton(self.rb_gerar)
        grupo_q.addButton(self.rb_existente)
        gq.addWidget(self.rb_gerar, 0, 0, 1, 2)
        gq.addWidget(QLabel("    Margem para juntar:"), 1, 0)
        self.spn_tol = QDoubleSpinBox()
        self.spn_tol.setRange(0.0, 5.0)
        self.spn_tol.setDecimals(3)
        self.spn_tol.setSingleStep(0.05)
        self.spn_tol.setSuffix(" m")
        self.spn_tol.setToolTip(
            "Frestas e sobreposições entre lotes até 2x este valor são fechadas\n"
            "na hora de formar a quadra.")
        gq.addWidget(self.spn_tol, 1, 1)
        self.chk_add_quadras = QCheckBox("Adicionar as quadras geradas ao projeto")
        gq.addWidget(self.chk_add_quadras, 2, 0, 1, 2)
        gq.addWidget(self.rb_existente, 3, 0, 1, 2)
        self.cmb_quadras = QgsMapLayerComboBox()
        self.cmb_quadras.setFilters(_filtro_poligono())
        gq.addWidget(self.cmb_quadras, 4, 0, 1, 2)
        gq.addWidget(QLabel("Distância lote ↔ contorno:"), 5, 0)
        self.spn_dist = QDoubleSpinBox()
        self.spn_dist.setRange(0.01, 10.0)
        self.spn_dist.setDecimals(2)
        self.spn_dist.setSingleStep(0.05)
        self.spn_dist.setSuffix(" m")
        self.spn_dist.setToolTip(
            "Até essa distância do contorno da quadra o lado do lote conta como\n"
            "face externa (testada). Aumente se a camada de quadras não bate\n"
            "exatamente com os lotes.")
        gq.addWidget(self.spn_dist, 5, 1)
        geral.addWidget(g_quadra)

        # --- Sentido ----------------------------------------------------
        g_sentido = QGroupBox("Sentido")
        hs = QHBoxLayout(g_sentido)
        self.rb_horario = QRadioButton("Horário")
        self.rb_anti = QRadioButton("Anti-horário")
        grupo_s = QButtonGroup(self)
        grupo_s.addButton(self.rb_horario)
        grupo_s.addButton(self.rb_anti)
        hs.addWidget(self.rb_horario)
        hs.addWidget(self.rb_anti)
        hs.addStretch()
        geral.addWidget(g_sentido)

        # --- Tipo -------------------------------------------------------
        g_tipo = QGroupBox("Tipo de numeração")
        gt = QGridLayout(g_tipo)
        self.rb_seq = QRadioButton("Sequencial (1, 2, 3…)")
        self.rb_per = QRadioButton("Por perímetro (metros pela face externa)")
        grupo_t = QButtonGroup(self)
        grupo_t.addButton(self.rb_seq)
        grupo_t.addButton(self.rb_per)
        gt.addWidget(self.rb_seq, 0, 0, 1, 4)
        gt.addWidget(QLabel("    Inicial:"), 1, 0)
        self.spn_inicial = QSpinBox()
        self.spn_inicial.setRange(0, 9999999)
        gt.addWidget(self.spn_inicial, 1, 1)
        gt.addWidget(QLabel("Incremento:"), 1, 2)
        self.spn_inc = QSpinBox()
        self.spn_inc.setRange(1, 1000)
        gt.addWidget(self.spn_inc, 1, 3)
        self.chk_internos = QCheckBox("Numerar também os lotes internos (sem face), no fim")
        gt.addWidget(self.chk_internos, 2, 0, 1, 4)
        gt.addWidget(self.rb_per, 3, 0, 1, 4)
        gt.addWidget(QLabel("    Medir até:"), 4, 0)
        self.cmb_ponto = QComboBox()
        for chave, rotulo in PONTOS_TESTADA:
            self.cmb_ponto.addItem(rotulo, chave)
        self.cmb_ponto.setToolTip(
            "O número é a distância em metros, pelo contorno, do ponto inicial\n"
            "até este ponto da testada do lote (arredondada; mínimo 1).")
        gt.addWidget(self.cmb_ponto, 4, 1, 1, 3)
        geral.addWidget(g_tipo)

        # --- Ponto inicial ----------------------------------------------
        g_ini = QGroupBox("Ponto inicial")
        gi = QGridLayout(g_ini)
        self.btn_clicar = QPushButton("Clicar no mapa")
        self.btn_clicar.setCheckable(True)
        self.btn_clicar.setToolTip(
            "Clique perto do contorno da quadra (perto de um vértice ele gruda\n"
            "no vértice). Um clique por quadra; o último vale.\n"
            "Botão direito desfaz o último clique; Esc sai.")
        self.btn_limpar = QPushButton("Limpar pontos")
        self.lbl_pontos = QLabel("Nenhum ponto clicado")
        gi.addWidget(self.btn_clicar, 0, 0)
        gi.addWidget(self.btn_limpar, 0, 1)
        gi.addWidget(self.lbl_pontos, 1, 0, 1, 2)
        gi.addWidget(QLabel("Quadra sem clique começa no:"), 2, 0, 1, 2)
        self.cmb_canto = QComboBox()
        for chave, rotulo in CANTOS_ROTULO:
            self.cmb_canto.addItem(rotulo, chave)
        gi.addWidget(self.cmb_canto, 3, 0, 1, 2)
        geral.addWidget(g_ini)

        # --- Saída ------------------------------------------------------
        self.chk_rotulo = QCheckBox("Ativar rótulo com o número (substitui o rótulo atual)")
        geral.addWidget(self.chk_rotulo)

        self.barra = QProgressBar()
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        self.barra.setTextVisible(False)
        self.barra.setMaximumHeight(8)
        geral.addWidget(self.barra)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        geral.addWidget(self.lbl_status)

        botoes = QHBoxLayout()
        self.btn_previa = QPushButton("Pré-visualizar")
        self.btn_numerar = QPushButton("Numerar")
        self.btn_numerar.setDefault(True)
        self.btn_fechar = QPushButton("Fechar")
        botoes.addWidget(self.btn_previa)
        botoes.addStretch()
        botoes.addWidget(self.btn_numerar)
        botoes.addWidget(self.btn_fechar)
        geral.addLayout(botoes)

        # sinais
        self.cmb_lotes.layerChanged.connect(self._camada_lotes_mudou)
        self.rb_gerar.toggled.connect(self._atualizar_estado)
        self.rb_seq.toggled.connect(self._atualizar_estado)
        self.rb_horario.toggled.connect(self._reorientar)
        self.cmb_canto.currentIndexChanged.connect(self._reorientar)
        self.btn_clicar.toggled.connect(self.alternar_ferramenta)
        self.btn_limpar.clicked.connect(self.limpar_pontos)
        self.btn_previa.clicked.connect(self.previsualizar)
        self.btn_numerar.clicked.connect(self.numerar)
        self.btn_fechar.clicked.connect(self.close)

    def _atualizar_estado(self, *args):
        gerar = self.rb_gerar.isChecked()
        self.spn_tol.setEnabled(gerar)
        self.chk_add_quadras.setEnabled(gerar)
        self.cmb_quadras.setEnabled(not gerar)
        seq = self.rb_seq.isChecked()
        self.spn_inicial.setEnabled(seq)
        self.spn_inc.setEnabled(seq)
        self.chk_internos.setEnabled(seq)
        self.cmb_ponto.setEnabled(not seq)

    def _camada_lotes_mudou(self, camada):
        texto = self.cmb_campo.currentText()
        self.cmb_campo.blockSignals(True)
        self.cmb_campo.clear()
        if camada is not None:
            for f in camada.fields():
                self.cmb_campo.addItem(f.name())
            self.chk_rotulo.setChecked(not camada.labelsEnabled())
        self.cmb_campo.setEditText(texto or "numero")
        self.cmb_campo.blockSignals(False)
        self._ctx = None

    # ------------------------------------------------------------------
    # Configuração lembrada
    # ------------------------------------------------------------------
    def _ler(self, chave, padrao):
        v = self.cfg.value(PREFIXO_CFG + chave, padrao)
        try:
            if isinstance(padrao, bool):
                return v in (True, "true", "True", 1, "1")
            if isinstance(padrao, int):
                return int(v)
            if isinstance(padrao, float):
                return float(v)
        except (TypeError, ValueError):
            return padrao
        return v if v is not None else padrao

    def _gravar(self, chave, valor):
        self.cfg.setValue(PREFIXO_CFG + chave, valor)

    def _carregar_config(self):
        projeto = QgsProject.instance()
        for camada in projeto.mapLayers().values():
            if not isinstance(camada, QgsVectorLayer):
                continue
            if camada.geometryType() != _tipo_geom("Polygon"):
                continue
            nome = camada.name().lower()
            if "lote" in nome:
                self.cmb_lotes.setLayer(camada)
            elif "quadra" in nome:
                self.cmb_quadras.setLayer(camada)
        self._camada_lotes_mudou(self.cmb_lotes.currentLayer())

        self.chk_selecionados.setChecked(self._ler("selecionados", True))
        self.cmb_campo.setEditText(self._ler("campo", "numero"))
        (self.rb_gerar if self._ler("gerar", True) else self.rb_existente).setChecked(True)
        self.spn_tol.setValue(self._ler("tolerancia", 0.10))
        self.chk_add_quadras.setChecked(self._ler("add_quadras", True))
        self.spn_dist.setValue(self._ler("distancia", 0.30))
        (self.rb_horario if self._ler("horario", True) else self.rb_anti).setChecked(True)
        (self.rb_seq if self._ler("sequencial", True) else self.rb_per).setChecked(True)
        self.spn_inicial.setValue(self._ler("inicial", 1))
        self.spn_inc.setValue(self._ler("incremento", 1))
        self.chk_internos.setChecked(self._ler("internos", False))
        i = self.cmb_ponto.findData(self._ler("ponto_testada", core.FIM))
        self.cmb_ponto.setCurrentIndex(max(i, 0))
        i = self.cmb_canto.findData(self._ler("canto", "NO"))
        self.cmb_canto.setCurrentIndex(max(i, 0))

    def _salvar_config(self):
        self._gravar("selecionados", self.chk_selecionados.isChecked())
        self._gravar("campo", self.cmb_campo.currentText().strip())
        self._gravar("gerar", self.rb_gerar.isChecked())
        self._gravar("tolerancia", self.spn_tol.value())
        self._gravar("add_quadras", self.chk_add_quadras.isChecked())
        self._gravar("distancia", self.spn_dist.value())
        self._gravar("horario", self.rb_horario.isChecked())
        self._gravar("sequencial", self.rb_seq.isChecked())
        self._gravar("inicial", self.spn_inicial.value())
        self._gravar("incremento", self.spn_inc.value())
        self._gravar("internos", self.chk_internos.isChecked())
        self._gravar("ponto_testada", self.cmb_ponto.currentData())
        self._gravar("canto", self.cmb_canto.currentData())

    # ------------------------------------------------------------------
    # Pontos iniciais
    # ------------------------------------------------------------------
    def alternar_ferramenta(self, ligado):
        if ligado:
            atual = self.canvas.mapTool()
            if atual is not self.ferramenta:
                self.ferramenta_anterior = atual
            self.canvas.setMapTool(self.ferramenta)
            self._status("Clique o ponto inicial perto do contorno de cada quadra. "
                         "Botão direito desfaz; Esc sai.")
        elif self.canvas.mapTool() is self.ferramenta:
            if self.ferramenta_anterior is not None:
                try:
                    self.canvas.setMapTool(self.ferramenta_anterior)
                except RuntimeError:
                    self.canvas.unsetMapTool(self.ferramenta)
            else:
                self.canvas.unsetMapTool(self.ferramenta)

    def sair_ferramenta(self):
        self.btn_clicar.setChecked(False)

    def ferramenta_desativada(self):
        if self.btn_clicar.isChecked():
            self.btn_clicar.blockSignals(True)
            self.btn_clicar.setChecked(False)
            self.btn_clicar.blockSignals(False)

    def adicionar_ponto(self, ponto, tolerancia):
        crs = self.canvas.mapSettings().destinationCrs()
        self.pontos.append((QgsPointXY(ponto), tolerancia, crs))
        m = QgsVertexMarker(self.canvas)
        m.setCenter(ponto)
        m.setIconType(_icone_marcador("ICON_X"))
        m.setColor(QColor(0, 150, 0))
        m.setIconSize(14)
        m.setPenWidth(3)
        self.marcadores.append(m)
        self._atualizar_lbl_pontos()
        self._reorientar()

    def desfazer_ponto(self):
        if not self.pontos:
            return
        self.pontos.pop()
        m = self.marcadores.pop()
        self.canvas.scene().removeItem(m)
        self._atualizar_lbl_pontos()
        self._reorientar()

    def limpar_pontos(self):
        self.pontos = []
        for m in self.marcadores:
            self.canvas.scene().removeItem(m)
        self.marcadores = []
        self._atualizar_lbl_pontos()
        self._reorientar()

    def _atualizar_lbl_pontos(self):
        n = len(self.pontos)
        self.lbl_pontos.setText("Nenhum ponto clicado" if n == 0 else
                                "1 ponto clicado" if n == 1 else f"{n} pontos clicados")

    def _pontos_na_src(self, crs_destino):
        saida = []
        projeto = QgsProject.instance()
        for ponto, tol, crs in self.pontos:
            if crs != crs_destino:
                try:
                    ct = QgsCoordinateTransform(crs, crs_destino, projeto)
                    ponto = QgsPointXY(ct.transform(ponto))
                except Exception:
                    continue
            saida.append((ponto, tol))
        return saida

    # ------------------------------------------------------------------
    # Preparação (quadras, contornos, origens)
    # ------------------------------------------------------------------
    def _status(self, texto):
        self.lbl_status.setText(texto)
        QApplication.processEvents()

    def _progresso(self, valor):
        self.barra.setValue(int(valor))
        QApplication.processEvents()

    def _aviso(self, texto):
        self.lbl_status.setText(texto)
        self.iface.messageBar().pushWarning(TITULO, texto)

    def _sentido(self):
        return core.HORARIO if self.rb_horario.isChecked() else core.ANTI_HORARIO

    def _params(self):
        return core.Parametros(
            sentido=self._sentido(),
            tipo=core.SEQUENCIAL if self.rb_seq.isChecked() else core.PERIMETRO,
            inicial=self.spn_inicial.value(),
            incremento=self.spn_inc.value(),
            ponto_testada=self.cmb_ponto.currentData(),
            numerar_internos=self.chk_internos.isChecked(),
            distancia_face=self.spn_dist.value(),
        )

    def _preparar(self):
        camada = self.cmb_lotes.currentLayer()
        if camada is None:
            self._aviso("Escolha a camada de lotes.")
            return None
        if camada.crs().isGeographic():
            self._aviso("A camada de lotes está em graus. Use uma SRC projetada em metros "
                        "(ex.: SIRGAS 2000 / UTM).")
            return None

        if self.chk_selecionados.isChecked():
            if camada.selectedFeatureCount() == 0:
                self._aviso("Nenhum lote selecionado. Selecione os lotes ou desmarque "
                            "'Somente lotes selecionados'.")
                return None
            feicoes = camada.getSelectedFeatures()
        else:
            feicoes = camada.getFeatures()

        lotes = {}
        for f in feicoes:
            g = f.geometry()
            if g is None or g.isNull() or g.isEmpty():
                continue
            lotes[f.id()] = QgsGeometry(g)
        if not lotes:
            self._aviso("Nenhum lote com geometria.")
            return None

        self._status(f"Montando as quadras de {len(lotes)} lote(s)...")
        self._progresso(5)
        if self.rb_gerar.isChecked():
            quadras = core.gerar_quadras(list(lotes.values()), self.spn_tol.value(),
                                         self._progresso)
        else:
            quadras = self._quadras_existentes(camada, lotes)
            if quadras is None:
                return None
        if not quadras:
            self._aviso("Nenhuma quadra encontrada para esses lotes.")
            return None

        self._progresso(75)
        grupos, fora = core.associar_lotes(lotes, quadras)
        contornos = {}
        for i, fids in grupos.items():
            if not fids:
                continue
            c = core.Contorno(quadras[i], self._sentido())
            if c.valido:
                contornos[i] = c
            else:
                fora.extend(fids)
        origem = core.definir_origens(contornos, quadras,
                                      self._pontos_na_src(camada.crs()),
                                      self.cmb_canto.currentData())
        return {
            "camada_id": camada.id(),
            "lotes": lotes,
            "quadras": quadras,
            "grupos": grupos,
            "fora": fora,
            "contornos": contornos,
            "origem": origem,
        }

    def _quadras_existentes(self, camada, lotes):
        cq = self.cmb_quadras.currentLayer()
        if cq is None:
            self._aviso("Escolha a camada de quadras.")
            return None
        if cq.id() == camada.id():
            self._aviso("A camada de quadras não pode ser a mesma dos lotes.")
            return None
        projeto = QgsProject.instance()
        caixas = [g.boundingBox() for g in lotes.values()]
        extensao = QgsRectangle(caixas[0])
        for c in caixas[1:]:
            extensao.combineExtentWith(c)
        para_lotes = QgsCoordinateTransform(cq.crs(), camada.crs(), projeto)
        para_quadras = QgsCoordinateTransform(camada.crs(), cq.crs(), projeto)
        req = QgsFeatureRequest().setFilterRect(para_quadras.transformBoundingBox(extensao))
        quadras = []
        for f in cq.getFeatures(req):
            g = f.geometry()
            if g is None or g.isNull() or g.isEmpty():
                continue
            g = QgsGeometry(g)
            if cq.crs() != camada.crs():
                g.transform(para_lotes)
            for parte in core.partes_poligono(g):
                quadras.append(parte)
        return quadras

    def _calcular(self, ctx):
        params = self._params()
        numeros, sem_numero, repetidos = {}, list(ctx["fora"]), 0
        total = max(len(ctx["contornos"]), 1)
        for k, (i, c) in enumerate(ctx["contornos"].items()):
            lista = [(fid, ctx["lotes"][fid]) for fid in ctx["grupos"][i]]
            nums, sem, _ordem = core.numerar_quadra(c, lista, params)
            numeros.update(nums)
            sem_numero.extend(sem)
            if params.tipo == core.PERIMETRO:
                valores = list(nums.values())
                repetidos += len(valores) - len(set(valores))
            if k % 20 == 0:
                self._progresso(75 + 20 * k / total)
        return numeros, sem_numero, repetidos

    # ------------------------------------------------------------------
    # Desenho no mapa
    # ------------------------------------------------------------------
    def _camada_ctx(self, ctx):
        return QgsProject.instance().mapLayer(ctx["camada_id"]) if ctx else None

    def _limpar_desenho(self):
        self.rb_quadras.reset(_tipo_geom("Polygon"))
        self.rb_setas.reset(_tipo_geom("Line"))
        for m in self.marcadores_inicio:
            self.canvas.scene().removeItem(m)
        self.marcadores_inicio = []

    def _desenhar(self, ctx):
        self._limpar_desenho()
        camada = self._camada_ctx(ctx)
        if camada is None:
            return
        destino = self.canvas.mapSettings().destinationCrs()
        ct = QgsCoordinateTransform(camada.crs(), destino, QgsProject.instance())
        for i, c in ctx["contornos"].items():
            self.rb_quadras.addGeometry(ctx["quadras"][i], camada)
            comp = min(30.0, max(4.0, 0.15 * c.comprimento))
            pts = c.trecho_inicial(comp)
            if len(pts) >= 2:
                self.rb_setas.addGeometry(QgsGeometry.fromPolylineXY(pts), camada)
                cabeca = comp * 0.3
                ponta = pts[-1]
                tras = c.ponto_em(max(comp - cabeca, 0.0))
                dx, dy = ponta.x() - tras.x(), ponta.y() - tras.y()
                n = math.hypot(dx, dy)
                if n > 0:
                    px, py = -dy / n * cabeca * 0.45, dx / n * cabeca * 0.45
                    asa = [QgsPointXY(tras.x() + px, tras.y() + py), ponta,
                           QgsPointXY(tras.x() - px, tras.y() - py)]
                    self.rb_setas.addGeometry(QgsGeometry.fromPolylineXY(asa), camada)
            m = QgsVertexMarker(self.canvas)
            m.setCenter(QgsPointXY(ct.transform(c.ponto_em(0.0))))
            m.setIconType(_icone_marcador("ICON_CIRCLE"))
            m.setColor(QColor(220, 0, 0))
            m.setIconSize(16)
            m.setPenWidth(3)
            self.marcadores_inicio.append(m)

    def _destacar_pendentes(self, ctx, fids):
        self.rb_pendentes.reset(_tipo_geom("Polygon"))
        camada = self._camada_ctx(ctx)
        if camada is None:
            return
        for fid in fids:
            g = ctx["lotes"].get(fid)
            if g is not None:
                self.rb_pendentes.addGeometry(g, camada)

    def _reorientar(self, *args):
        """Sentido, canto ou clique mudou: refaz só as setas da última prévia."""
        ctx = self._ctx
        camada = self._camada_ctx(ctx)
        if camada is None:
            return
        sentido = self._sentido()
        ctx["contornos"] = {i: core.Contorno(ctx["quadras"][i], sentido)
                            for i in ctx["contornos"]}
        ctx["origem"] = core.definir_origens(ctx["contornos"], ctx["quadras"],
                                             self._pontos_na_src(camada.crs()),
                                             self.cmb_canto.currentData())
        self._desenhar(ctx)

    # ------------------------------------------------------------------
    # Ações
    # ------------------------------------------------------------------
    def _ocupado(self, sim):
        for b in (self.btn_previa, self.btn_numerar, self.btn_clicar, self.btn_limpar):
            b.setEnabled(not sim)
        if sim:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _resumo_origens(self, ctx):
        clicadas = sum(1 for v in ctx["origem"].values() if v == "clique")
        total = len(ctx["contornos"])
        if clicadas == 0:
            return f"{total} quadra(s), todas começando no canto."
        return f"{total} quadra(s): {clicadas} pelo clique, {total - clicadas} pelo canto."

    def previsualizar(self):
        self._ocupado(True)
        try:
            ctx = self._preparar()
            if ctx is None:
                return
            numeros, sem_numero, repetidos = self._calcular(ctx)
            self._ctx = ctx
            self._desenhar(ctx)
            self._destacar_pendentes(ctx, sem_numero)
            texto = (f"{self._resumo_origens(ctx)} {len(numeros)} lote(s) seriam numerados. "
                     "Círculo vermelho = ponto inicial; seta = sentido.")
            if sem_numero:
                texto += f" {len(sem_numero)} ficariam sem número (em laranja)."
            if repetidos:
                texto += f" {repetidos} número(s) repetido(s) na mesma quadra."
            self._status(texto)
        finally:
            self._progresso(0)
            self._ocupado(False)

    def numerar(self):
        nome_campo = self.cmb_campo.currentText().strip()
        if not nome_campo:
            self._aviso("Informe o campo do número.")
            return
        self._ocupado(True)
        try:
            ctx = self._preparar()
            if ctx is None:
                return
            numeros, sem_numero, repetidos = self._calcular(ctx)
            self._ctx = ctx
            self._desenhar(ctx)
            self._destacar_pendentes(ctx, sem_numero)
            if not numeros:
                self._aviso("Nenhum lote encostou no contorno da quadra. Confira a distância "
                            "lote ↔ contorno.")
                return
            camada = self._camada_ctx(ctx)
            if not self._gravar_numeros(camada, nome_campo, numeros):
                return
            if self.rb_gerar.isChecked() and self.chk_add_quadras.isChecked():
                self._publicar_quadras(ctx, camada)
            if self.chk_rotulo.isChecked():
                self._ativar_rotulo(camada, nome_campo)
            self._salvar_config()

            texto = (f"{len(numeros)} lote(s) numerados no campo '{nome_campo}'. "
                     f"{self._resumo_origens(ctx)}")
            if sem_numero:
                texto += (f" {len(sem_numero)} lote(s) ficaram sem número "
                          "(sem face no contorno ou fora de quadra), em laranja no mapa.")
            if repetidos:
                texto += f" {repetidos} número(s) repetido(s) na mesma quadra."
            texto += " As alterações estão pendentes: confira e salve a camada (Ctrl+Z desfaz)."
            self._status(texto)
            self.iface.messageBar().pushSuccess(
                TITULO, f"{len(numeros)} lote(s) numerados; edição pendente de salvar.")
        finally:
            self._progresso(0)
            self._ocupado(False)

    def _gravar_numeros(self, camada, nome_campo, numeros):
        if not camada.isEditable() and not camada.startEditing():
            self._aviso("Não foi possível colocar a camada de lotes em edição.")
            return False
        camada.beginEditCommand("Numerar lotes")
        try:
            idx = camada.fields().indexFromName(nome_campo)
            if idx < 0:
                if not camada.addAttribute(_campo(nome_campo, "int")):
                    raise RuntimeError(f"Não foi possível criar o campo '{nome_campo}'.")
                idx = camada.fields().indexFromName(nome_campo)
                if idx < 0:
                    raise RuntimeError(f"O campo '{nome_campo}' não apareceu na camada.")
            numerico = camada.fields().at(idx).isNumeric()
            for fid, n in numeros.items():
                camada.changeAttributeValue(fid, idx, n if numerico else str(n))
        except Exception as ex:
            camada.destroyEditCommand()
            self._aviso(str(ex))
            return False
        camada.endEditCommand()
        camada.triggerRepaint()
        if self.cmb_campo.findText(nome_campo) < 0:
            self.cmb_campo.addItem(nome_campo)
        return True

    def _publicar_quadras(self, ctx, camada_lotes):
        projeto = QgsProject.instance()
        alvo = projeto.mapLayer(self._id_camada_quadras) if self._id_camada_quadras else None
        if alvo is None:
            alvo = QgsVectorLayer("Polygon", NOME_CAMADA_QUADRAS, "memory")
            alvo.setCrs(camada_lotes.crs())
            alvo.dataProvider().addAttributes([
                _campo("quadra", "int"), _campo("lotes", "int"),
                _campo("perimetro", "double"), _campo("area", "double"),
            ])
            alvo.updateFields()
            simbolo = QgsFillSymbol.createSimple({
                "color": "0,0,0,0", "outline_color": "30,120,255",
                "outline_width": "0.6", "outline_style": "dash",
            })
            alvo.renderer().setSymbol(simbolo)
            projeto.addMapLayer(alvo)
            self._id_camada_quadras = alvo.id()
        else:
            alvo.dataProvider().truncate()
        feicoes = []
        for n, i in enumerate(sorted(ctx["contornos"]), start=1):
            g = ctx["quadras"][i]
            f = QgsFeature(alvo.fields())
            f.setGeometry(g)
            f.setAttributes([n, len(ctx["grupos"][i]),
                             round(ctx["contornos"][i].comprimento, 2), round(g.area(), 2)])
            feicoes.append(f)
        alvo.dataProvider().addFeatures(feicoes)
        alvo.updateExtents()
        alvo.triggerRepaint()

    def _ativar_rotulo(self, camada, nome_campo):
        s = QgsPalLayerSettings()
        s.fieldName = nome_campo
        s.isExpression = False
        s.placement = _posicionamento_centro()
        fmt = QgsTextFormat()
        fmt.setSize(10)
        fmt.setColor(QColor(200, 0, 0))
        fundo = QgsTextBufferSettings()
        fundo.setEnabled(True)
        fundo.setSize(1)
        fundo.setColor(QColor(255, 255, 255))
        fmt.setBuffer(fundo)
        s.setFormat(fmt)
        camada.setLabeling(QgsVectorLayerSimpleLabeling(s))
        camada.setLabelsEnabled(True)
        camada.triggerRepaint()

    # ------------------------------------------------------------------
    # Fechamento
    # ------------------------------------------------------------------
    def limpar_tudo(self):
        self._ctx = None
        self.btn_clicar.setChecked(False)
        if self.canvas.mapTool() is self.ferramenta:
            self.canvas.unsetMapTool(self.ferramenta)
        self._limpar_desenho()
        self.rb_pendentes.reset(_tipo_geom("Polygon"))
        self.limpar_pontos()

    def closeEvent(self, e):
        self._salvar_config()
        self.limpar_tudo()
        super().closeEvent(e)

    def reject(self):
        self._salvar_config()
        self.limpar_tudo()
        super().reject()

    def remover_do_mapa(self):
        """Chamado ao descarregar o plugin."""
        self.limpar_tudo()
        for rb in (self.rb_quadras, self.rb_setas, self.rb_pendentes):
            self.canvas.scene().removeItem(rb)
