# -*- coding: utf-8 -*-
"""Atualizador Geocodificação: várias regras (campo + expressão) numa camada.

Baseado no complemento "Atualizador Geocodificação" 1.0.0, agora dentro do Ferramentas Solo.
As alterações ficam na edição da camada (nada é salvo automaticamente)."""
import json
import os

from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
    QProgressDialog, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from qgis.core import QgsExpression, QgsFeatureRequest, QgsProject

from .comum import (AJUSTAR, DESMARCADO, ESTICAR, ITEM_ATIVO, ITEM_MARCAVEL, ITEM_SELECIONAVEL,
                    LINHAS, MARCADO, MODAL_JANELA, NAO, SIM, camadas_vetoriais, contexto, nulo)


class AtualizadorDialog(QDialog):
    def __init__(self, iface, abrir_varias=None):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.abrir_varias = abrir_varias          # abre a janela "Expressão em várias camadas"
        self.setWindowTitle("Atualizador Geocodificação")
        self.resize(950, 620)
        self._build_ui()
        self.refresh_layers()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Configure uma regra por campo. As expressões usam a sintaxe da Calculadora de "
            "Expressões do QGIS e podem consultar outras camadas do projeto."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        linha_camada = QHBoxLayout()
        self.layer_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self.refresh_fields)
        linha_camada.addWidget(self.layer_combo, 1)
        if self.abrir_varias is not None:
            botao_varias = QPushButton("Várias camadas…")
            botao_varias.setToolTip("Rodar uma expressão em várias camadas de uma vez")
            botao_varias.clicked.connect(self.abrir_varias)
            linha_camada.addWidget(botao_varias)
        form.addRow("Camada que será atualizada:", linha_camada)
        layout.addLayout(form)

        self.selected_only = QCheckBox("Atualizar somente as feições selecionadas")
        self.selected_only.setChecked(True)
        layout.addWidget(self.selected_only)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Campo de destino", "Expressão QGIS", "Ativa"])
        self.table.horizontalHeader().setSectionResizeMode(0, AJUSTAR)
        self.table.horizontalHeader().setSectionResizeMode(1, ESTICAR)
        self.table.horizontalHeader().setSectionResizeMode(2, AJUSTAR)
        self.table.setSelectionBehavior(LINHAS)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        for label, callback in [
            ("Adicionar regra", self.add_rule),
            ("Remover regra", self.remove_rule),
            ("Validar expressões", self.validate_rules),
            ("Salvar configuração…", self.save_config),
            ("Carregar configuração…", self.load_config),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _c=False, cb=callback: cb())
            row.addWidget(btn)
        layout.addLayout(row)

        bottom = QHBoxLayout()
        self.status = QLabel("Pronto.")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.run_button = QPushButton("Executar atualização")
        self.run_button.clicked.connect(self.execute_rules)
        self.run_button.setDefault(True)
        bottom.addWidget(self.run_button)
        close_btn = QPushButton("Fechar")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self.add_rule()

    def refresh_layers(self):
        current_id = self.layer_combo.currentData() if self.layer_combo.count() else None
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for layer, _grupos in camadas_vetoriais():
            self.layer_combo.addItem(layer.name(), layer.id())
        idx = self.layer_combo.findData(current_id) if current_id else -1
        if idx >= 0:
            self.layer_combo.setCurrentIndex(idx)
        self.layer_combo.blockSignals(False)
        self.refresh_fields()

    def target_layer(self):
        layer_id = self.layer_combo.currentData()
        return QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def refresh_fields(self):
        layer = self.target_layer()
        fields = [f.name() for f in layer.fields()] if layer else []
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 0)
            if not isinstance(combo, QComboBox):
                continue
            old = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(fields)
            idx = combo.findText(old)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def add_rule(self, field="", expression=""):
        r = self.table.rowCount()
        self.table.insertRow(r)
        combo = QComboBox()
        layer = self.target_layer()
        if layer:
            combo.addItems([f.name() for f in layer.fields()])
        if field:
            idx = combo.findText(field)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self.table.setCellWidget(r, 0, combo)
        expr_item = QTableWidgetItem(expression)
        self.table.setItem(r, 1, expr_item)
        active = QTableWidgetItem()
        active.setFlags(active.flags() | ITEM_MARCAVEL | ITEM_ATIVO | ITEM_SELECIONAVEL)
        active.setCheckState(MARCADO)
        self.table.setItem(r, 2, active)
        self.table.setRowHeight(r, 48)

    def remove_rule(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            if self.table.rowCount():
                rows = [self.table.rowCount() - 1]
        for r in rows:
            self.table.removeRow(r)

    def rules(self):
        result = []
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 0)
            expr_item = self.table.item(r, 1)
            active_item = self.table.item(r, 2)
            field = combo.currentText() if combo else ""
            expression = expr_item.text().strip() if expr_item else ""
            active = active_item.checkState() == MARCADO if active_item else True
            if active and field and expression:
                result.append({"field": field, "expression": expression})
        return result

    def validate_rules(self):
        layer = self.target_layer()
        if not layer:
            QMessageBox.warning(self, "Validação", "Escolha a camada de destino.")
            return
        rules = self.rules()
        if not rules:
            QMessageBox.warning(self, "Validação", "Adicione ao menos uma regra ativa com campo e expressão.")
            return
        errors = []
        field_names = [f.name() for f in layer.fields()]
        for i, rule in enumerate(rules, 1):
            if rule["field"] not in field_names:
                errors.append(f"Regra {i}: campo de destino inexistente: {rule['field']}")
                continue
            exp = QgsExpression(rule["expression"])
            if exp.hasParserError():
                errors.append(f"Regra {i} ({rule['field']}): {exp.parserErrorString()}")
                continue
            exp.prepare(contexto(layer))
            if exp.hasEvalError():
                errors.append(f"Regra {i} ({rule['field']}): {exp.evalErrorString()}")
        if errors:
            QMessageBox.warning(self, "Expressões com problemas", "\n\n".join(errors))
            self.status.setText(f"Validação encontrou {len(errors)} problema(s).")
        else:
            QMessageBox.information(self, "Validação", f"{len(rules)} regra(s) sintaticamente válida(s).\n\nA validação não garante que cada expressão produzirá o resultado esperado em todas as feições.")
            self.status.setText(f"{len(rules)} regra(s) validadas.")

    def save_config(self):
        path, _ = QFileDialog.getSaveFileName(self, "Salvar configuração", "regras_geocodificacao.json", "Configuração JSON (*.json)")
        if not path:
            return
        data = {
            "format_version": 1,
            "target_layer_name": self.layer_combo.currentText(),
            "selected_only": self.selected_only.isChecked(),
            "rules": []
        }
        for r in range(self.table.rowCount()):
            combo = self.table.cellWidget(r, 0)
            expr = self.table.item(r, 1)
            active = self.table.item(r, 2)
            data["rules"].append({
                "field": combo.currentText() if combo else "",
                "expression": expr.text() if expr else "",
                "active": active.checkState() == MARCADO if active else True
            })
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.status.setText(f"Configuração salva: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Erro ao salvar", str(e))

    def load_config(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Carregar configuração", "", "Configuração JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.table.setRowCount(0)
            for rule in data.get("rules", []):
                self.add_rule(rule.get("field", ""), rule.get("expression", ""))
                item = self.table.item(self.table.rowCount() - 1, 2)
                item.setCheckState(MARCADO if rule.get("active", True) else DESMARCADO)
            self.selected_only.setChecked(bool(data.get("selected_only", True)))
            target_name = data.get("target_layer_name", "")
            idx = self.layer_combo.findText(target_name)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)
            self.refresh_fields()
            for r, rule in enumerate(data.get("rules", [])):   # campo de novo, já com a camada certa
                combo = self.table.cellWidget(r, 0)
                i = combo.findText(rule.get("field", "")) if combo else -1
                if i >= 0:
                    combo.setCurrentIndex(i)
            self.status.setText(f"Configuração carregada: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "Erro ao carregar", f"Não foi possível carregar a configuração:\n{e}")

    def execute_rules(self):
        layer = self.target_layer()
        if not layer:
            QMessageBox.warning(self, "Atualização", "Escolha a camada de destino.")
            return

        rules = self.rules()
        if not rules:
            QMessageBox.warning(self, "Atualização", "Não há regras ativas preenchidas.")
            return

        expressions = []
        for rule in rules:
            idx = layer.fields().indexFromName(rule["field"])
            if idx < 0:
                QMessageBox.warning(self, "Atualização", f"O campo '{rule['field']}' não existe na camada.")
                return
            exp = QgsExpression(rule["expression"])
            if exp.hasParserError():
                QMessageBox.warning(self, "Expressão inválida", f"Campo: {rule['field']}\n{exp.parserErrorString()}")
                return
            expressions.append((rule["field"], idx, exp))

        selected = self.selected_only.isChecked()
        if selected:
            ids = layer.selectedFeatureIds()
            if not ids:
                QMessageBox.information(self, "Atualização", "A opção de selecionados está ativa, mas nenhuma feição está selecionada.")
                return
            request = QgsFeatureRequest().setFilterFids(ids)
            total = len(ids)
        else:
            request = QgsFeatureRequest()
            total = layer.featureCount()

        reply = QMessageBox.question(
            self, "Confirmar atualização",
            f"Camada: {layer.name()}\nFeições previstas: {total}\nRegras ativas: {len(expressions)}\n\nAs alterações ficarão na edição da camada. Deseja continuar?",
            SIM | NAO, NAO
        )
        if reply != SIM:
            return
        if not layer.isEditable():
            if not layer.startEditing():
                QMessageBox.critical(self, "Atualização", "Não foi possível iniciar a edição da camada.")
                return

        progress = QProgressDialog("Atualizando atributos…", "Cancelar", 0, max(total, 1), self)
        progress.setWindowModality(MODAL_JANELA)
        progress.setMinimumDuration(0)
        changed = 0
        skipped = 0
        errors = []
        canceled = False
        layer.beginEditCommand("Atualizador Geocodificação")
        try:
            for n, feature in enumerate(layer.getFeatures(request), 1):
                if progress.wasCanceled():
                    break
                context = contexto(layer, feature)
                updates = []
                for field_name, idx, exp in expressions:
                    exp.prepare(context)
                    value = exp.evaluate(context)
                    if exp.hasEvalError():
                        errors.append(f"Feição {feature.id()}, campo {field_name}: {exp.evalErrorString()}")
                        continue
                    if nulo(value):
                        # Null é um resultado válido e será gravado se a expressão retornar NULL.
                        pass
                    updates.append((idx, value))
                feature_changed = False
                for idx, value in updates:
                    if not feature.setAttribute(idx, value):
                        errors.append(f"Feição {feature.id()}: não foi possível definir o campo {layer.fields()[idx].name()}.")
                        continue
                    feature_changed = True
                if feature_changed:
                    if layer.updateFeature(feature):
                        changed += 1
                    else:
                        skipped += 1
                progress.setValue(n)
                if n % 25 == 0:
                    progress.setLabelText(f"Processando feição {n} de aproximadamente {total}…")
            canceled = progress.wasCanceled()   # antes de fechar: fechar conta como "cancelar" no Qt
            layer.endEditCommand()
        except Exception as e:
            layer.destroyEditCommand()
            QMessageBox.critical(self, "Erro durante atualização", f"A operação foi interrompida e o comando de edição foi desfeito:\n{e}")
            return
        finally:
            progress.close()

        layer.triggerRepaint()
        summary = (
            f"Processamento concluído.\n\nFeições alteradas: {changed}\n"
            f"Feições não atualizadas: {skipped}\nErros de expressão/campo: {len(errors)}\n\n"
            "As alterações ainda estão na sessão de edição do QGIS. Confira os resultados e use Salvar Edições ou Reverter Edições na camada."
        )
        if canceled:
            summary = "Processamento cancelado pelo usuário.\n\n" + summary
        if errors:
            summary += "\n\nPrimeiros erros:\n" + "\n".join(errors[:12])
            if len(errors) > 12:
                summary += f"\n… e mais {len(errors) - 12} erro(s)."
        QMessageBox.information(self, "Resumo da atualização", summary)
        self.status.setText(f"Concluído: {changed} feição(ões) alteradas; {len(errors)} erro(s).")
