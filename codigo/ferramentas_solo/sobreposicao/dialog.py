# -*- coding: utf-8 -*-
"""Janela do Seleção por Sobreposição."""

import time

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)
from qgis.core import Qgis, QgsField, QgsProject, QgsSettings
from qgis.gui import QgsMapLayerComboBox

from . import core

try:  # QGIS 3.34+
    FILTRO_POLIGONO = Qgis.LayerFilter.PolygonLayer
except AttributeError:  # QGIS antigo
    from qgis.core import QgsMapLayerProxyModel
    FILTRO_POLIGONO = QgsMapLayerProxyModel.PolygonLayer

CHAVE = "SelecaoSobreposicao/"

OPERADORES = [
    (">", "acima de"),
    (">=", "a partir de (igual ou acima)"),
    ("<", "abaixo de"),
    ("<=", "até (igual ou abaixo)"),
]

BASES = [
    (core.BASE_ALVO, "da área do polígono a selecionar"),
    (core.BASE_REF, "da área do polígono da outra camada"),
    (core.BASE_MAIOR, "de qualquer um dos dois (o maior)"),
]

MODOS = [
    ("nova", "Nova seleção"),
    ("adicionar", "Adicionar à seleção atual"),
    ("remover", "Remover da seleção atual"),
    ("dentro", "Filtrar dentro da seleção atual"),
]


def _novo_campo_double(nome):
    """QgsField decimal compatível com QGIS 3.x antigo e novo."""
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(nome, QMetaType.Type.Double, "double", 10, 2)
    except (TypeError, AttributeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(nome, QVariant.Double, "double", 10, 2)


class DialogoSobreposicao(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self._ref_conectada = None
        self.setWindowTitle("Seleção por sobreposição")
        self.setMinimumWidth(500)
        self._montar()
        self._carregar_config()
        self._atualizar_tudo()

    # ------------------------------------------------------------------ UI
    def _montar(self):
        lay = QVBoxLayout(self)

        def grupo(titulo):
            gb = QGroupBox(titulo)
            v = QVBoxLayout(gb)
            f = QFormLayout()
            f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            v.addLayout(f)
            lay.addWidget(gb)
            return v, f

        # Camadas
        v1, f1 = grupo("Camadas")
        self.cb_alvo = QgsMapLayerComboBox()
        self.cb_alvo.setFilters(FILTRO_POLIGONO)
        self.cb_ref = QgsMapLayerComboBox()
        self.cb_ref.setFilters(FILTRO_POLIGONO)
        self.ck_sel_ref = QCheckBox("Só as feições selecionadas")
        f1.addRow("Selecionar em:", self.cb_alvo)
        f1.addRow("Comparar com:", self.cb_ref)
        f1.addRow("", self.ck_sel_ref)

        # Regra
        v2, f2 = grupo("Regra")
        linha = QHBoxLayout()
        self.cb_oper = QComboBox()
        for cod, txt in OPERADORES:
            self.cb_oper.addItem(txt, cod)
        self.sp_pct = QDoubleSpinBox()
        self.sp_pct.setRange(0.0, 100.0)
        self.sp_pct.setDecimals(2)
        self.sp_pct.setSingleStep(5.0)
        self.sp_pct.setSuffix(" %")
        self.sp_pct.setValue(20.0)
        linha.addWidget(self.cb_oper, 1)
        linha.addWidget(self.sp_pct)
        f2.addRow("Sobreposição:", linha)

        self.cb_base = QComboBox()
        for cod, txt in BASES:
            self.cb_base.addItem(txt, cod)
        f2.addRow("Percentual:", self.cb_base)

        self.ck_somar = QCheckBox("Somar várias feições da outra camada")
        self.ck_somar.setChecked(True)
        self.ck_somar.setToolTip(
            "Ligado: um polígono coberto 12% por um lote e 10% por outro conta 22%.\n"
            "Desligado: vale só a maior sobreposição com uma única feição (12%).\n"
            "Onde as feições da outra camada se sobrepõem entre si, a área não é contada duas vezes."
        )
        f2.addRow("", self.ck_somar)

        self.ck_zero = QCheckBox("Ignorar quem não sobrepõe nada")
        self.ck_zero.setChecked(True)
        self.ck_zero.setToolTip("Só vale para 'abaixo de' / 'até': evita selecionar\n"
                                "os polígonos com 0% (que nem tocam a outra camada).")
        f2.addRow("", self.ck_zero)

        self.lb_resumo = QLabel()
        self.lb_resumo.setWordWrap(True)
        self.lb_resumo.setStyleSheet("color: #555; font-style: italic;")
        v2.addWidget(self.lb_resumo)

        # Seleção
        v3, f3 = grupo("Seleção")
        self.cb_modo = QComboBox()
        for cod, txt in MODOS:
            self.cb_modo.addItem(txt, cod)
        f3.addRow("Modo:", self.cb_modo)

        linha_campo = QHBoxLayout()
        self.ck_campo = QCheckBox("Gravar o % no campo")
        self.ed_campo = QLineEdit("sobrep_pct")
        self.ed_campo.setMaxLength(30)
        linha_campo.addWidget(self.ck_campo)
        linha_campo.addWidget(self.ed_campo, 1)
        v3.addLayout(linha_campo)
        lb_aviso = QLabel("A camada fica em edição, sem salvar — confira e salve você.")
        lb_aviso.setWordWrap(True)
        lb_aviso.setStyleSheet("color: #777; font-size: 8pt;")
        v3.addWidget(lb_aviso)

        self.ck_zoom = QCheckBox("Dar zoom na seleção")
        v3.addWidget(self.ck_zoom)

        # Resultado + botões
        self.lb_resultado = QLabel(" ")
        self.lb_resultado.setWordWrap(True)
        lay.addWidget(self.lb_resultado)

        bts = QHBoxLayout()
        bts.addStretch(1)
        self.bt_sel = QPushButton("Selecionar")
        self.bt_sel.setDefault(True)
        self.bt_fechar = QPushButton("Fechar")
        bts.addWidget(self.bt_sel)
        bts.addWidget(self.bt_fechar)
        lay.addLayout(bts)

        # Sinais
        self.bt_sel.clicked.connect(self.executar)
        self.bt_fechar.clicked.connect(self.close)
        self.cb_alvo.layerChanged.connect(self._atualizar_tudo)
        self.cb_ref.layerChanged.connect(self._ref_mudou)
        for w in (self.cb_oper, self.cb_base, self.cb_modo):
            w.currentIndexChanged.connect(self._atualizar_tudo)
        self.sp_pct.valueChanged.connect(self._atualizar_tudo)
        self.ck_somar.toggled.connect(self._atualizar_tudo)
        self.ck_sel_ref.toggled.connect(self._atualizar_tudo)
        self.ck_campo.toggled.connect(self._atualizar_tudo)

    # ----------------------------------------------------------- estados
    def _ref_mudou(self, *_):
        if self._ref_conectada is not None:
            try:
                self._ref_conectada.selectionChanged.disconnect(self._atualizar_tudo)
            except (TypeError, RuntimeError):
                pass
            self._ref_conectada = None
        ref = self.cb_ref.currentLayer()
        if ref is not None:
            ref.selectionChanged.connect(self._atualizar_tudo)
            self._ref_conectada = ref
        self._atualizar_tudo()

    def _atualizar_tudo(self, *_):
        alvo = self.cb_alvo.currentLayer()
        ref = self.cb_ref.currentLayer()
        oper = self.cb_oper.currentData()
        base = self.cb_base.currentData()

        n_sel = ref.selectedFeatureCount() if ref is not None else 0
        self.ck_sel_ref.setText("Só as feições selecionadas (%d)" % n_sel)

        self.ck_somar.setEnabled(base != core.BASE_REF)
        self.ck_zero.setEnabled(oper in ("<", "<="))
        self.ed_campo.setEnabled(self.ck_campo.isChecked())

        if alvo is None or ref is None:
            self.lb_resumo.setText("Escolha as duas camadas.")
            self.bt_sel.setEnabled(False)
            return
        self.bt_sel.setEnabled(True)

        txt_oper = self.cb_oper.currentText().split(" (")[0]
        pct = self.sp_pct.value()
        pct_txt = ("%.2f" % pct).rstrip("0").rstrip(".").replace(".", ",")
        if base == core.BASE_ALVO:
            txt_base = "da área do próprio polígono"
        elif base == core.BASE_REF:
            txt_base = "da área do polígono de '%s'" % ref.name()
        else:
            txt_base = "da área de qualquer um dos dois"
        soma = ""
        if base != core.BASE_REF:
            soma = (" (somando todas as feições que o cobrem)" if self.ck_somar.isChecked()
                    else " (com uma única feição)")
        mesma = ""
        if alvo.id() == ref.id():
            mesma = " Mesma camada: cada polígono é comparado com os outros, não com ele mesmo."
        self.lb_resumo.setText(
            "Seleciona os polígonos de '%s' cuja área sobreposta a '%s' fica %s %s%% %s%s.%s"
            % (alvo.name(), ref.name(), txt_oper, pct_txt, txt_base, soma, mesma))

    # ----------------------------------------------------------- config
    def _carregar_config(self):
        s = QgsSettings()
        proj = QgsProject.instance()
        for cb, k in ((self.cb_alvo, "alvo"), (self.cb_ref, "ref")):
            lyr = proj.mapLayer(s.value(CHAVE + k, "", type=str))
            if lyr is not None:
                cb.setLayer(lyr)

        def set_data(cb, valor):
            i = cb.findData(valor)
            if i >= 0:
                cb.setCurrentIndex(i)

        set_data(self.cb_oper, s.value(CHAVE + "oper", ">", type=str))
        set_data(self.cb_base, s.value(CHAVE + "base", core.BASE_ALVO, type=str))
        set_data(self.cb_modo, s.value(CHAVE + "modo", "nova", type=str))
        self.sp_pct.setValue(s.value(CHAVE + "pct", 20.0, type=float))
        self.ck_somar.setChecked(s.value(CHAVE + "somar", True, type=bool))
        self.ck_zero.setChecked(s.value(CHAVE + "zero", True, type=bool))
        self.ck_sel_ref.setChecked(s.value(CHAVE + "sel_ref", False, type=bool))
        self.ck_campo.setChecked(s.value(CHAVE + "campo", False, type=bool))
        self.ed_campo.setText(s.value(CHAVE + "nome_campo", "sobrep_pct", type=str))
        self.ck_zoom.setChecked(s.value(CHAVE + "zoom", False, type=bool))
        self._ref_mudou()

    def _salvar_config(self):
        s = QgsSettings()
        alvo, ref = self.cb_alvo.currentLayer(), self.cb_ref.currentLayer()
        s.setValue(CHAVE + "alvo", alvo.id() if alvo else "")
        s.setValue(CHAVE + "ref", ref.id() if ref else "")
        s.setValue(CHAVE + "oper", self.cb_oper.currentData())
        s.setValue(CHAVE + "base", self.cb_base.currentData())
        s.setValue(CHAVE + "modo", self.cb_modo.currentData())
        s.setValue(CHAVE + "pct", self.sp_pct.value())
        s.setValue(CHAVE + "somar", self.ck_somar.isChecked())
        s.setValue(CHAVE + "zero", self.ck_zero.isChecked())
        s.setValue(CHAVE + "sel_ref", self.ck_sel_ref.isChecked())
        s.setValue(CHAVE + "campo", self.ck_campo.isChecked())
        s.setValue(CHAVE + "nome_campo", self.ed_campo.text().strip())
        s.setValue(CHAVE + "zoom", self.ck_zoom.isChecked())

    # ----------------------------------------------------------- execução
    def executar(self):
        alvo = self.cb_alvo.currentLayer()
        ref = self.cb_ref.currentLayer()
        if alvo is None or ref is None:
            return
        if not (core.eh_poligono(alvo) and core.eh_poligono(ref)):
            QMessageBox.warning(self, "Seleção por sobreposição",
                                "As duas camadas precisam ser de polígono.")
            return

        so_sel_ref = self.ck_sel_ref.isChecked()
        if so_sel_ref and ref.selectedFeatureCount() == 0:
            QMessageBox.warning(self, "Seleção por sobreposição",
                                "A opção 'só as feições selecionadas' está marcada, "
                                "mas não há nada selecionado em '%s'." % ref.name())
            return

        modo = self.cb_modo.currentData()
        sel_atual = set(alvo.selectedFeatureIds())
        ids_avaliar = None
        if modo in ("remover", "dentro"):
            if not sel_atual:
                QMessageBox.information(self, "Seleção por sobreposição",
                                        "Não há seleção atual em '%s' para %s."
                                        % (alvo.name(), "remover" if modo == "remover" else "filtrar"))
                return
            ids_avaliar = sel_atual

        nome_campo = self.ed_campo.text().strip()
        gravar = self.ck_campo.isChecked()
        if gravar and not nome_campo:
            QMessageBox.warning(self, "Seleção por sobreposição", "Informe o nome do campo.")
            return

        self._salvar_config()

        total = len(ids_avaliar) if ids_avaliar is not None else alvo.featureCount()
        prog = QProgressDialog("Calculando sobreposição...", "Cancelar", 0, 100, self)
        prog.setWindowTitle("Seleção por sobreposição")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(400)
        prog.setValue(0)

        def progresso(pct, txt):
            prog.setLabelText(txt)
            prog.setValue(int(pct))
            QApplication.processEvents()
            return prog.wasCanceled()

        t0 = time.time()
        try:
            res = core.calcular_sobreposicao(
                alvo, ref,
                ids_alvo=ids_avaliar,
                so_selecionadas_ref=so_sel_ref,
                base=self.cb_base.currentData(),
                somar=self.ck_somar.isChecked(),
                progresso=progresso,
            )
        except core.Cancelado:
            prog.close()
            self.lb_resultado.setText("Cancelado — a seleção não foi alterada.")
            return
        except Exception as e:  # noqa
            prog.close()
            QMessageBox.critical(self, "Seleção por sobreposição", "Erro no cálculo:\n%s" % e)
            return
        prog.close()

        oper = self.cb_oper.currentData()
        limite = self.sp_pct.value()
        ign_zero = self.ck_zero.isChecked()
        atendem = {fid for fid, p in res.items() if core.atende(p, oper, limite, ign_zero)}

        if modo == "nova":
            final = atendem
        elif modo == "adicionar":
            final = sel_atual | atendem
        elif modo == "remover":
            final = sel_atual - atendem
        else:
            final = sel_atual & atendem

        alvo.selectByIds(list(final))

        msg_campo = ""
        if gravar:
            msg_campo = self._gravar_campo(alvo, nome_campo, res)

        if self.ck_zoom.isChecked() and final:
            self.iface.mapCanvas().zoomToSelected(alvo)

        tocam = sum(1 for p in res.values() if p > core.EPS)
        dt = time.time() - t0
        txt = ("<b>%d</b> polígono(s) atendem a regra — %d avaliados, %d com alguma sobreposição. "
               "Seleção final em '%s': <b>%d</b>. (%.1f s)"
               % (len(atendem), len(res), tocam, alvo.name(), len(final), dt))
        if msg_campo:
            txt += "<br>" + msg_campo
        self.lb_resultado.setText(txt)
        self.iface.messageBar().pushMessage(
            "Seleção por sobreposição",
            "%d selecionado(s) em '%s'." % (len(final), alvo.name()),
            level=Qgis.Success, duration=4)

    def _gravar_campo(self, alvo, nome, res):
        caps = alvo.dataProvider().capabilities()
        if not alvo.isEditable():
            if not alvo.startEditing():
                return "Não deu para colocar '%s' em edição — percentual não gravado." % alvo.name()
        idx = alvo.fields().indexOf(nome)
        if idx < 0:
            try:
                from qgis.core import QgsVectorDataProvider
                pode_criar = bool(caps & QgsVectorDataProvider.AddAttributes)
            except Exception:
                pode_criar = True
            if not pode_criar or not alvo.addAttribute(_novo_campo_double(nome)):
                return "Não foi possível criar o campo '%s'." % nome
            idx = alvo.fields().indexOf(nome)
            if idx < 0:
                return "Não foi possível criar o campo '%s'." % nome
        alvo.beginEditCommand("Percentual de sobreposição (%s)" % nome)
        for fid, p in res.items():
            alvo.changeAttributeValue(fid, idx, round(p, 2))
        alvo.endEditCommand()
        return ("Percentual gravado em '%s' para %d polígono(s) — camada em edição, "
                "ainda NÃO salva." % (nome, len(res)))

    # ----------------------------------------------------------- fechar
    def closeEvent(self, ev):
        try:
            self._salvar_config()
        except Exception:
            pass
        super().closeEvent(ev)
