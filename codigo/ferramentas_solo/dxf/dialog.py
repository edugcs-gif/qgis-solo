import os

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QButtonGroup,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QApplication,
)
from qgis.PyQt.QtCore import Qt, QStandardPaths

from .logic import OPCOES, FeedbackSimples


class DialogoImportarCAD(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Importar CAD (DXF)")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("O que você deseja importar?"))

        self.grupo_botoes = QButtonGroup(self)
        for i, (rotulo, _funcao) in enumerate(OPCOES):
            rb = QRadioButton(rotulo)
            if i == 0:
                rb.setChecked(True)
            self.grupo_botoes.addButton(rb, i)
            layout.addWidget(rb)

        layout.addWidget(QLabel("Arquivo DXF:"))
        linha_arquivo = QHBoxLayout()
        self.campo_arquivo = QLineEdit()
        self.campo_arquivo.setPlaceholderText("Selecione o arquivo .dxf...")
        self.campo_arquivo.setText(self._caminho_padrao())
        botao_procurar = QPushButton("Procurar...")
        botao_procurar.clicked.connect(self.procurar_arquivo)
        linha_arquivo.addWidget(self.campo_arquivo)
        linha_arquivo.addWidget(botao_procurar)
        layout.addLayout(linha_arquivo)

        linha_botoes = QHBoxLayout()
        botao_executar = QPushButton("Executar")
        botao_executar.clicked.connect(self.executar)
        botao_cancelar = QPushButton("Cancelar")
        botao_cancelar.clicked.connect(self.reject)
        linha_botoes.addStretch()
        linha_botoes.addWidget(botao_executar)
        linha_botoes.addWidget(botao_cancelar)
        layout.addLayout(linha_botoes)

    def _caminho_padrao(self):
        """Área de Trabalho\\dxf\\DXF.dxf do usuário logado, calculado na
        hora (funciona em qualquer PC/usuário)."""
        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        if not desktop:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        return os.path.join(desktop, "dxf", "DXF.dxf")

    def procurar_arquivo(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Selecionar arquivo DXF", "", "Arquivos DXF (*.dxf)"
        )
        if caminho:
            self.campo_arquivo.setText(caminho)

    def executar(self):
        caminho = self.campo_arquivo.text().strip()
        if not caminho:
            QMessageBox.warning(self, "Importar CAD", "Selecione um arquivo DXF antes de continuar.")
            return

        if not os.path.exists(caminho):
            QMessageBox.warning(self, "Importar CAD", "O arquivo selecionado não foi encontrado.")
            return

        indice = self.grupo_botoes.checkedId()
        rotulo, funcao = OPCOES[indice]

        feedback = FeedbackSimples()
        self.setCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            mensagem = funcao(caminho, feedback)
            QMessageBox.information(self, "Importar CAD", mensagem)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Importar CAD", f"Erro ao importar '{rotulo}':\n{str(e)}")
        finally:
            self.unsetCursor()
