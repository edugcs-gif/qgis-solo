# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QFormLayout, QComboBox, QLabel,
    QCheckBox, QDialogButtonBox, QMessageBox, QCompleter,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsSettings, QgsFieldProxyModel, Qgis,
    QgsFields,
)
from qgis.gui import QgsFieldComboBox

SETTINGS_KEY = "PreencherCampo"
MAX_VALORES_LISTA = 300


class PreencherCampoDialog(QDialog):
    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.setWindowTitle("Preencher campo")
        self.setMinimumWidth(360)

        n = layer.selectedFeatureCount()
        info = QLabel(f"<b>{n}</b> feição(ões) selecionada(s) em <b>{layer.name()}</b>")

        # Campos editáveis (ignora campos de junção/virtuais e somente-leitura)
        editaveis = QgsFields()
        for i, f in enumerate(layer.fields()):
            origem = layer.fields().fieldOrigin(i)
            if origem in (QgsFields.OriginJoin, QgsFields.OriginExpression):
                continue
            if layer.editFormConfig().readOnly(i):
                continue
            editaveis.append(f)

        self.cbCampo = QgsFieldComboBox()
        self.cbCampo.setFields(editaveis)

        self.cbValor = QComboBox()
        self.cbValor.setEditable(True)
        self.cbValor.setInsertPolicy(QComboBox.NoInsert)
        comp = self.cbValor.completer()
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCompletionMode(QCompleter.PopupCompletion)

        self.chkNulo = QCheckBox("Definir como NULL (limpar campo)")
        self.chkNulo.toggled.connect(lambda c: self.cbValor.setEnabled(not c))

        form = QFormLayout()
        form.addRow("Campo:", self.cbCampo)
        form.addRow("Valor:", self.cbValor)

        botoes = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botoes.button(QDialogButtonBox.Ok).setText("Aplicar")
        botoes.accepted.connect(self.accept)
        botoes.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addWidget(self.chkNulo)
        lay.addWidget(botoes)

        self.cbCampo.fieldChanged.connect(self._carregar_valores)

        # Lembra o último campo usado nesta camada
        s = QgsSettings()
        ultimo = s.value(f"{SETTINGS_KEY}/campo/{layer.id()}", "")
        if ultimo and editaveis.indexFromName(ultimo) >= 0:
            self.cbCampo.setField(ultimo)
        self._carregar_valores(self.cbCampo.currentField())
        ultimo_valor = s.value(f"{SETTINGS_KEY}/valor/{layer.id()}/{self.cbCampo.currentField()}", None)
        if ultimo_valor is not None:
            self._selecionar_valor(ultimo_valor)

        self.cbValor.setFocus()
        self.cbValor.lineEdit().selectAll()

    # ---------- valores ----------
    def _value_map(self, idx):
        setup = self.layer.editorWidgetSetup(idx)
        if setup.type() != "ValueMap":
            return None
        mp = setup.config().get("map", {})
        pares = []
        if isinstance(mp, dict):
            pares = list(mp.items())
        else:  # lista de dicts {descricao: valor}
            for d in mp:
                pares.extend(d.items())
        return [(desc, val) for desc, val in pares if not str(val).startswith("{2839923C")]

    def _carregar_valores(self, nome_campo):
        self.cbValor.clear()
        idx = self.layer.fields().indexFromName(nome_campo)
        if idx < 0:
            return
        vm = self._value_map(idx)
        if vm:
            for desc, val in vm:
                self.cbValor.addItem(str(desc), val)
        else:
            vals = [v for v in self.layer.uniqueValues(idx, MAX_VALORES_LISTA)
                    if v is not None and not (isinstance(v, QVariant) and v.isNull())]
            for v in sorted(vals, key=lambda x: str(x)):
                self.cbValor.addItem(str(v), v)
        self.cbValor.setCurrentIndex(-1)
        self.cbValor.setEditText("")

    def _selecionar_valor(self, valor):
        i = self.cbValor.findData(valor)
        if i < 0:
            i = self.cbValor.findText(str(valor))
        if i >= 0:
            self.cbValor.setCurrentIndex(i)
        else:
            self.cbValor.setEditText(str(valor))

    def valor(self):
        """Retorna (ok, valor). Valor None = NULL."""
        if self.chkNulo.isChecked():
            return True, None
        texto = self.cbValor.currentText()
        i = self.cbValor.findText(texto, Qt.MatchExactly)
        if i >= 0 and self.cbValor.itemData(i) is not None:
            return True, self.cbValor.itemData(i)
        return True, texto


class PreencherCampoPlugin:
    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar          # barra do Ferramentas Solo
        self.menu = menu or "Preencher Campo"
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Preencher campo das feições selecionadas",
                              self.iface.mainWindow())
        self.action.setObjectName("PreencherCampoAction")
        self.action.triggered.connect(self.run)
        if self.toolbar is not None:
            self.toolbar.addAction(self.action)
        else:
            self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu(self.menu, self.action)
        # permite definir atalho em Configurações > Atalhos do teclado
        self.iface.registerMainWindowAction(self.action, "")

    def unload(self):
        if self.action is None:
            return
        self.iface.unregisterMainWindowAction(self.action)
        self.iface.removePluginVectorMenu(self.menu, self.action)
        if self.toolbar is not None:
            try:
                self.toolbar.removeAction(self.action)
            except RuntimeError:
                pass
        else:
            self.iface.removeToolBarIcon(self.action)
        self.action.deleteLater()
        self.action = None

    def _msg(self, texto, nivel=Qgis.Warning):
        self.iface.messageBar().pushMessage("Preencher campo", texto, level=nivel, duration=5)

    def run(self):
        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer):
            self._msg("Selecione uma camada vetorial no painel de camadas.")
            return
        if layer.selectedFeatureCount() == 0:
            self._msg(f"Nenhuma feição selecionada em '{layer.name()}'.")
            return
        if layer.readOnly():
            self._msg(f"A camada '{layer.name()}' é somente leitura.", Qgis.Critical)
            return

        dlg = PreencherCampoDialog(layer, self.iface.mainWindow())
        if not dlg.exec_():
            return

        nome = dlg.cbCampo.currentField()
        idx = layer.fields().indexFromName(nome)
        if idx < 0:
            self._msg("Escolha um campo.")
            return
        _, valor = dlg.valor()
        campo = layer.fields().at(idx)

        # Converte o texto para o tipo do campo
        if valor is not None:
            if isinstance(valor, str) and valor.strip() == "":
                resp = QMessageBox.question(
                    self.iface.mainWindow(), "Preencher campo",
                    "Valor vazio. Deseja gravar NULL no campo?",
                    QMessageBox.Yes | QMessageBox.No)
                if resp != QMessageBox.Yes:
                    return
                valor = None
            else:
                ok, conv = self._converter(campo, valor)
                if not ok:
                    self._msg(f"Valor '{valor}' não é compatível com o campo "
                              f"'{nome}' ({campo.typeName()}).", Qgis.Critical)
                    return
                valor = conv

        if not layer.isEditable():
            if not layer.startEditing():
                self._msg("Não foi possível iniciar a edição da camada.", Qgis.Critical)
                return

        ids = layer.selectedFeatureIds()
        layer.beginEditCommand(f"Preencher '{nome}' em {len(ids)} feições")
        falhas = 0
        for fid in ids:
            if not layer.changeAttributeValue(fid, idx, valor):
                falhas += 1
        layer.endEditCommand()
        layer.triggerRepaint()

        s = QgsSettings()
        s.setValue(f"{SETTINGS_KEY}/campo/{layer.id()}", nome)
        s.setValue(f"{SETTINGS_KEY}/valor/{layer.id()}/{nome}", "" if valor is None else valor)

        txt_val = "NULL" if valor is None else str(valor)
        if falhas:
            self._msg(f"{len(ids) - falhas} preenchidas, {falhas} falharam.", Qgis.Warning)
        else:
            self._msg(f"'{nome}' = {txt_val} em {len(ids)} feição(ões). "
                      "Edição pendente — salve para gravar (Ctrl+Z desfaz).", Qgis.Success)

    @staticmethod
    def _converter(campo, valor):
        if not isinstance(valor, str):
            return True, valor
        v = valor.strip()
        if campo.isNumeric():
            v = v.replace(",", ".") if v.count(",") == 1 and "." not in v else v
        try:
            res = campo.convertCompatible(v)  # QGIS 3.x
            if isinstance(res, tuple):
                return res
            return True, res
        except Exception:
            return False, None
