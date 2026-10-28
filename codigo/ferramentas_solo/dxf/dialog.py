import os

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QButtonGroup,
    QComboBox,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QFrame,
    QMessageBox,
    QApplication,
)
from qgis.PyQt.QtCore import Qt, QStandardPaths
from qgis.core import QgsSettings

from .logic import OPCOES, FORMATOS, ORDEM_FORMATOS, Cancelado, Destino, FeedbackSimples

CHAVE = "FerramentasSolo/dxf_importar/"


def _enum(base, *nomes):
    """Enum do Qt5 (QMessageBox.AcceptRole) ou do Qt6 (QMessageBox.ButtonRole.AcceptRole)."""
    for nome in nomes:
        valor = base
        try:
            for parte in nome.split("."):
                valor = getattr(valor, parte)
            return valor
        except AttributeError:
            continue
    raise AttributeError(nomes)


PAPEL_ACEITAR = _enum(QMessageBox, "ButtonRole.AcceptRole", "AcceptRole")
PAPEL_ACAO = _enum(QMessageBox, "ButtonRole.ActionRole", "ActionRole")
PAPEL_CANCELAR = _enum(QMessageBox, "ButtonRole.RejectRole", "RejectRole")
SEM_CONFIRMAR = _enum(QFileDialog, "Option.DontConfirmOverwrite", "DontConfirmOverwrite")
CURSOR_ESPERA = _enum(Qt, "CursorShape.WaitCursor", "WaitCursor")


class DialogoImportarCAD(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Importar CAD (DXF)")
        self.setMinimumWidth(480)
        self.ajustes = QgsSettings()

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
        self.campo_arquivo.textChanged.connect(self._atualizar_destino)
        botao_procurar = QPushButton("Procurar...")
        botao_procurar.clicked.connect(self.procurar_arquivo)
        linha_arquivo.addWidget(self.campo_arquivo)
        linha_arquivo.addWidget(botao_procurar)
        layout.addLayout(linha_arquivo)

        # ---------------- onde as feições ficam salvas
        linha = QFrame()
        linha.setFrameShape(_enum(QFrame, "Shape.HLine", "HLine"))
        linha.setFrameShadow(_enum(QFrame, "Shadow.Sunken", "Sunken"))
        layout.addWidget(linha)

        linha_formato = QHBoxLayout()
        linha_formato.addWidget(QLabel("Salvar as feições em:"))
        self.combo_formato = QComboBox()
        for chave in ORDEM_FORMATOS:
            self.combo_formato.addItem(FORMATOS[chave][0], chave)
        linha_formato.addWidget(self.combo_formato, 1)
        layout.addLayout(linha_formato)

        self.rotulo_destino = QLabel()
        layout.addWidget(self.rotulo_destino)
        linha_destino = QHBoxLayout()
        self.campo_destino = QLineEdit()
        self.campo_destino.textChanged.connect(self._atualizar_destino)
        self.botao_destino = QPushButton("Procurar...")
        self.botao_destino.clicked.connect(self.procurar_destino)
        linha_destino.addWidget(self.campo_destino)
        linha_destino.addWidget(self.botao_destino)
        layout.addLayout(linha_destino)
        self.dica_destino = QLabel()
        self.dica_destino.setWordWrap(True)
        self.dica_destino.setStyleSheet("color: #666;")
        layout.addWidget(self.dica_destino)

        formato = self.ajustes.value(CHAVE + "formato", "gpkg")
        i = self.combo_formato.findData(formato)
        self.combo_formato.setCurrentIndex(i if i >= 0 else 0)
        self._formato_anterior = None
        self.combo_formato.currentIndexChanged.connect(self._trocar_formato)
        self._trocar_formato()

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
        desktop = QStandardPaths.writableLocation(
            _enum(QStandardPaths, "StandardLocation.DesktopLocation", "DesktopLocation"))
        if not desktop:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        return os.path.join(desktop, "dxf", "DXF.dxf")

    # ---------------- formato e destino
    def formato(self):
        return self.combo_formato.currentData() or "memoria"

    def _trocar_formato(self, *_):
        """Mostra o campo certo (arquivo .gpkg ou pasta) e o último destino usado nesse formato."""
        if self._formato_anterior is not None:
            self.ajustes.setValue(CHAVE + "destino_" + self._formato_anterior, self.campo_destino.text().strip())
        chave = self.formato()
        self._formato_anterior = chave
        _rot, _drv, ext, arquivo_unico = FORMATOS[chave]
        salvar = ext is not None
        for w in (self.rotulo_destino, self.campo_destino, self.botao_destino):
            w.setVisible(salvar)
        if not salvar:
            self.dica_destino.setText("As camadas ficam só na memória do QGIS e somem ao fechar o projeto, "
                                      "a menos que você as salve depois (botão direito > Tornar permanente).")
            return
        self.rotulo_destino.setText("Arquivo GeoPackage:" if arquivo_unico else "Pasta de destino:")
        self.campo_destino.blockSignals(True)
        self.campo_destino.setText(self.ajustes.value(CHAVE + "destino_" + chave, ""))
        self.campo_destino.blockSignals(False)
        self._atualizar_destino()

    def _atualizar_destino(self, *_):
        chave = self.formato()
        _rot, _drv, ext, arquivo_unico = FORMATOS[chave]
        if ext is None:
            return
        dxf = self.campo_arquivo.text().strip() or "DXF.dxf"
        alvo = Destino(chave, self.campo_destino.text()).alvo(dxf)
        if arquivo_unico:
            self.campo_destino.setPlaceholderText("Vazio = mesmo local e nome do DXF")
            self.dica_destino.setText(
                "Cada layer do CAD vira uma camada dentro do GeoPackage (ex.: LOTE_poligonos). "
                "Se o arquivo já existe, as outras camadas dele continuam lá.\nVai salvar em: " + alvo)
        else:
            self.campo_destino.setPlaceholderText("Vazio = mesma pasta do DXF")
            nome = os.path.splitext(os.path.basename(dxf))[0]
            self.dica_destino.setText(
                "Um arquivo %s por layer do CAD (ex.: %s_LOTE_poligonos%s).\nVai salvar em: %s"
                % (ext, nome, ext, alvo))

    def procurar_arquivo(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Selecionar arquivo DXF", "", "Arquivos DXF (*.dxf)"
        )
        if caminho:
            self.campo_arquivo.setText(caminho)

    def procurar_destino(self):
        chave = self.formato()
        _rot, _drv, ext, arquivo_unico = FORMATOS[chave]
        inicio = self.campo_destino.text().strip() or Destino(chave).alvo(
            self.campo_arquivo.text().strip() or self._caminho_padrao())
        if arquivo_unico:
            # sem a pergunta "substituir o arquivo?": o GeoPackage existente não é apagado
            caminho, _ = QFileDialog.getSaveFileName(
                self, "Salvar no GeoPackage", inicio, "GeoPackage (*.gpkg)", options=SEM_CONFIRMAR)
        else:
            caminho = QFileDialog.getExistingDirectory(self, "Pasta de destino", inicio)
        if caminho:
            self.campo_destino.setText(caminho)

    def _perguntar_existentes(self, nomes, onde):
        lista = "\n".join("  • " + n for n in nomes[:15]) + ("\n  ..." if len(nomes) > 15 else "")
        caixa = QMessageBox(self)
        caixa.setWindowTitle("Importar CAD")
        caixa.setIcon(_enum(QMessageBox, "Icon.Question", "Question"))
        caixa.setText("Já existe(m) %d camada(s) com esse nome em:\n%s\n\n%s" % (len(nomes), onde, lista))
        caixa.setInformativeText("Substituir apaga o conteúdo anterior dessas camadas. "
                                 "Criar com outro nome mantém as antigas e grava as novas com _2, _3...")
        b_novo = caixa.addButton("Criar com outro nome", PAPEL_ACEITAR)
        b_subst = caixa.addButton("Substituir", PAPEL_ACAO)
        caixa.addButton("Cancelar", PAPEL_CANCELAR)
        caixa.setDefaultButton(b_novo)
        self.unsetCursor()
        (caixa.exec_ if hasattr(caixa, "exec_") else caixa.exec)()
        self.setCursor(CURSOR_ESPERA)
        clicado = caixa.clickedButton()
        if clicado is b_subst:
            return "substituir"
        if clicado is b_novo:
            return "renomear"
        return None

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
        chave = self.formato()
        destino = Destino(chave, self.campo_destino.text(), self._perguntar_existentes)
        self.ajustes.setValue(CHAVE + "formato", chave)
        if FORMATOS[chave][2] is not None:
            self.ajustes.setValue(CHAVE + "destino_" + chave, self.campo_destino.text().strip())

        feedback = FeedbackSimples()
        self.setCursor(CURSOR_ESPERA)
        QApplication.processEvents()
        try:
            mensagem = funcao(caminho, feedback, destino)
            QMessageBox.information(self, "Importar CAD", mensagem)
            self.accept()
        except Cancelado:
            pass                     # desistiu na pergunta: o diálogo continua aberto
        except Exception as e:
            QMessageBox.critical(self, "Importar CAD", f"Erro ao importar '{rotulo}':\n{str(e)}")
        finally:
            self.unsetCursor()
