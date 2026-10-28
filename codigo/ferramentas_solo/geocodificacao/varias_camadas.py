# -*- coding: utf-8 -*-
"""Expressão em várias camadas: marque as camadas, escolha um dos campos que todas têm e rode a
mesma expressão em todas de uma vez.

- Os campos em comum são comparados pelo nome, sem diferenciar maiúsculas (SETOR = setor).
- Cada camada usa a própria feição na expressão ("setor" é o setor da feição daquela camada).
- Só grava quando o valor muda; um Ctrl+Z por camada desfaz. Nada é salvo automaticamente.
"""
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QProgressDialog, QPushButton, QSplitter, QVBoxLayout,
)
from qgis.core import QgsExpression, QgsFeatureRequest, QgsIconUtils, QgsProject
from qgis.gui import QgsExpressionLineEdit

from .comum import (DESMARCADO, HORIZONTAL, ITEM_ATIVO, ITEM_MARCAVEL, MARCADO, MODAL_JANELA, NAO,
                    PAPEL_DADO, PAPEL_NOME, SIM, camada_aceita_edicao, camadas_vetoriais, campo_gravavel,
                    contexto, iguais, tipo_amigavel)

TITULO = "Expressão em várias camadas"
TODOS_ATRIBUTOS = getattr(QgsFeatureRequest, "ALL_ATTRIBUTES", "#!allattributes!#")
MAX_PREVIA = 4


def _sem_geometria():
    try:
        from qgis.core import Qgis
        return Qgis.FeatureRequestFlag.NoGeometry
    except AttributeError:
        return QgsFeatureRequest.NoGeometry


class ExpressaoVariasCamadasDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle(TITULO)
        self.resize(980, 640)
        self.comuns = []          # [(nome exibido, {id_camada: nome real do campo}, gravável, tipo)]
        self._montar()
        self.atualizar_camadas(marcar_do_painel=True)

    # ------------------------------------------------------------------ tela
    def _montar(self):
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Marque as camadas, escolha um dos campos que todas elas têm e escreva a expressão. "
            "Ela roda em cada camada com a feição daquela camada (sintaxe da Calculadora de Campos "
            "do QGIS). As alterações ficam na edição de cada camada.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        divisor = QSplitter(HORIZONTAL)
        # --- camadas
        caixa_c = QGroupBox("Camadas")
        lc = QVBoxLayout(caixa_c)
        self.filtro = QLineEdit()
        self.filtro.setPlaceholderText("Filtrar camadas pelo nome...")
        self.filtro.textChanged.connect(self._filtrar)
        lc.addWidget(self.filtro)
        self.lista_camadas = QListWidget()
        self.lista_camadas.itemChanged.connect(self._camadas_mudaram)
        lc.addWidget(self.lista_camadas)
        linha = QHBoxLayout()
        for texto, marcar in (("Marcar todas", True), ("Desmarcar todas", False)):
            b = QPushButton(texto)
            b.clicked.connect(lambda _c=False, m=marcar: self._marcar_visiveis(m))
            linha.addWidget(b)
        b = QPushButton("Atualizar lista")
        b.setToolTip("Lê de novo as camadas do projeto e as feições selecionadas")
        b.clicked.connect(lambda: self.atualizar_camadas())
        linha.addWidget(b)
        lc.addLayout(linha)
        divisor.addWidget(caixa_c)
        # --- campos em comum
        caixa_f = QGroupBox("Campos em comum")
        lf = QVBoxLayout(caixa_f)
        self.lista_campos = QListWidget()
        self.lista_campos.setToolTip("Clique para escolher o campo que recebe o resultado; "
                                     "duplo clique coloca o campo na expressão")
        self.lista_campos.itemClicked.connect(self._campo_clicado)
        self.lista_campos.itemDoubleClicked.connect(self._inserir_campo)
        lf.addWidget(self.lista_campos)
        self.rotulo_campos = QLabel()
        self.rotulo_campos.setWordWrap(True)
        self.rotulo_campos.setStyleSheet("color: #666;")
        lf.addWidget(self.rotulo_campos)
        divisor.addWidget(caixa_f)
        divisor.setSizes([520, 440])
        lay.addWidget(divisor, 1)

        # --- destino e expressão
        linha_dest = QHBoxLayout()
        linha_dest.addWidget(QLabel("Campo que recebe o resultado:"))
        self.combo_destino = QComboBox()
        self.combo_destino.setMinimumWidth(260)
        linha_dest.addWidget(self.combo_destino)
        linha_dest.addStretch()
        lay.addLayout(linha_dest)

        lay.addWidget(QLabel("Expressão (o botão ε abre o construtor de expressões):"))
        self.expressao = QgsExpressionLineEdit()
        self.expressao.setMultiLine(True)
        self.expressao.setExpressionDialogTitle(TITULO)
        self.expressao.setMinimumHeight(90)
        self.expressao.expressionChanged.connect(lambda _t: self._previa())
        lay.addWidget(self.expressao)

        self.rotulo_previa = QLabel()
        self.rotulo_previa.setWordWrap(True)
        self.rotulo_previa.setTextFormat(_rich())
        lay.addWidget(self.rotulo_previa)

        self.so_selecionadas = QCheckBox("Somente as feições selecionadas de cada camada "
                                         "(camadas sem seleção ficam de fora)")
        self.so_selecionadas.toggled.connect(lambda _c: (self._atualizar_textos(), self._previa()))
        lay.addWidget(self.so_selecionadas)

        rodape = QHBoxLayout()
        self.status = QLabel("Pronto.")
        self.status.setWordWrap(True)
        rodape.addWidget(self.status, 1)
        b_val = QPushButton("Validar")
        b_val.clicked.connect(lambda: self.validar(mostrar=True))
        rodape.addWidget(b_val)
        self.botao_executar = QPushButton("Executar em todas")
        self.botao_executar.setDefault(True)
        self.botao_executar.clicked.connect(self.executar)
        rodape.addWidget(self.botao_executar)
        b_fechar = QPushButton("Fechar")
        b_fechar.clicked.connect(self.close)
        rodape.addWidget(b_fechar)
        lay.addLayout(rodape)

    # ------------------------------------------------------------------ camadas
    def atualizar_camadas(self, marcar_do_painel=False):
        marcadas = set(self.ids_marcados())
        if marcar_do_painel and not marcadas:
            try:     # camadas selecionadas no painel já vêm marcadas
                marcadas = {l.id() for l in self.iface.layerTreeView().selectedLayers()}
            except Exception:
                marcadas = set()
        camadas = camadas_vetoriais()
        nomes = [l.name() for l, _g in camadas]
        self.lista_camadas.blockSignals(True)
        self.lista_camadas.clear()
        for lyr, grupos in camadas:
            item = QListWidgetItem()
            item.setData(PAPEL_DADO, lyr.id())
            item.setData(PAPEL_NOME, lyr.name() if nomes.count(lyr.name()) == 1 or not grupos
                         else "%s  —  %s" % (lyr.name(), grupos))
            try:
                item.setIcon(QgsIconUtils.iconForLayer(lyr))
            except Exception:
                pass
            item.setFlags(ITEM_MARCAVEL | ITEM_ATIVO)
            item.setCheckState(MARCADO if lyr.id() in marcadas else DESMARCADO)
            self.lista_camadas.addItem(item)
        self.lista_camadas.blockSignals(False)
        self._atualizar_textos()
        self._filtrar(self.filtro.text())
        self._camadas_mudaram()

    def _atualizar_textos(self):
        so_sel = self.so_selecionadas.isChecked()
        self.lista_camadas.blockSignals(True)
        for i in range(self.lista_camadas.count()):
            item = self.lista_camadas.item(i)
            lyr = QgsProject.instance().mapLayer(item.data(PAPEL_DADO))
            if lyr is None:
                continue
            nsel = lyr.selectedFeatureCount()
            n = lyr.featureCount()
            info = "%d %s" % (n, "feição" if n == 1 else "feições")
            if nsel:
                info += " · %d selecionada(s)" % nsel
            elif so_sel:
                info += " · nenhuma selecionada"
            item.setText("%s   (%s)" % (item.data(PAPEL_NOME), info))
        self.lista_camadas.blockSignals(False)

    def _filtrar(self, texto):
        t = (texto or "").strip().lower()
        for i in range(self.lista_camadas.count()):
            item = self.lista_camadas.item(i)
            item.setHidden(bool(t) and t not in item.text().lower())

    def _marcar_visiveis(self, marcar):
        self.lista_camadas.blockSignals(True)
        for i in range(self.lista_camadas.count()):
            item = self.lista_camadas.item(i)
            if not item.isHidden():
                item.setCheckState(MARCADO if marcar else DESMARCADO)
        self.lista_camadas.blockSignals(False)
        self._camadas_mudaram()

    def ids_marcados(self):
        return [self.lista_camadas.item(i).data(PAPEL_DADO) for i in range(self.lista_camadas.count())
                if self.lista_camadas.item(i).checkState() == MARCADO]

    def camadas_marcadas(self):
        prj = QgsProject.instance()
        return [l for l in (prj.mapLayer(i) for i in self.ids_marcados()) if l is not None]

    def marcar(self, camadas):
        """Marca exatamente estas camadas (usado também pelos testes)."""
        ids = {l.id() for l in camadas}
        self.lista_camadas.blockSignals(True)
        for i in range(self.lista_camadas.count()):
            item = self.lista_camadas.item(i)
            item.setCheckState(MARCADO if item.data(PAPEL_DADO) in ids else DESMARCADO)
        self.lista_camadas.blockSignals(False)
        self._camadas_mudaram()

    # ------------------------------------------------------------------ campos em comum
    def _camadas_mudaram(self, *_):
        camadas = self.camadas_marcadas()
        anterior = self.combo_destino.currentText()
        self.comuns = []
        if camadas:
            base = camadas[0]
            for campo in base.fields():
                chave = campo.name().lower()
                nomes, tipos, gravavel, grafias = {}, set(), True, []
                for lyr in camadas:
                    idx = next((i for i, f in enumerate(lyr.fields()) if f.name().lower() == chave), -1)
                    if idx < 0:
                        break
                    f = lyr.fields()[idx]
                    nomes[lyr.id()] = f.name()
                    grafias.append(f.name())
                    tipos.add(tipo_amigavel(f))
                    gravavel = gravavel and campo_gravavel(lyr, idx)
                else:
                    tipo = tipos.pop() if len(tipos) == 1 else "tipos diferentes: " + ", ".join(sorted(tipos))
                    # nome como aparece na maioria das camadas (SETOR / setor)
                    nome = max(grafias, key=lambda g: (grafias.count(g), -grafias.index(g)))
                    self.comuns.append((nome, nomes, gravavel, tipo))
        self.lista_campos.clear()
        self.combo_destino.clear()
        for nome, _nomes, gravavel, tipo in self.comuns:
            item = QListWidgetItem("%s   ·   %s%s" % (nome, tipo, "" if gravavel else "   (só leitura)"))
            item.setData(PAPEL_DADO, nome)
            self.lista_campos.addItem(item)
            if gravavel:
                self.combo_destino.addItem(nome)
        i = self.combo_destino.findText(anterior)
        if i >= 0:
            self.combo_destino.setCurrentIndex(i)
        if not camadas:
            self.rotulo_campos.setText("Marque uma ou mais camadas.")
        else:
            self.rotulo_campos.setText("%d campo(s) em comum entre %d camada(s)." % (len(self.comuns), len(camadas))
                                       + (" Nenhum campo existe em todas as camadas marcadas." if not self.comuns else ""))
        self.expressao.setLayer(camadas[0] if camadas else None)
        self._previa()

    def _campo_clicado(self, item):
        i = self.combo_destino.findText(item.data(PAPEL_DADO))
        if i >= 0:
            self.combo_destino.setCurrentIndex(i)

    def _inserir_campo(self, item):
        nome = item.data(PAPEL_DADO)
        atual = self.expressao.expression()
        self.expressao.setExpression((atual + " " if atual and not atual.endswith(" ") else atual)
                                     + QgsExpression.quotedColumnRef(nome))

    # ------------------------------------------------------------------ expressão
    def _feicoes(self, lyr, req=None):
        req = QgsFeatureRequest(req) if req is not None else QgsFeatureRequest()
        if self.so_selecionadas.isChecked():
            ids = lyr.selectedFeatureIds()
            if not ids:
                return None, 0
            req.setFilterFids(ids)
            return req, len(ids)
        return req, lyr.featureCount()

    def _previa(self):
        texto = self.expressao.expression().strip()
        camadas = self.camadas_marcadas()
        if not texto or not camadas:
            self.rotulo_previa.setText("")
            return
        exp = QgsExpression(texto)
        if exp.hasParserError():
            self.rotulo_previa.setText("<span style='color:#b00020'>Expressão com erro: %s</span>"
                                       % _esc(exp.parserErrorString()))
            return
        partes = []
        for lyr in camadas[:MAX_PREVIA]:
            req, n = self._feicoes(lyr)
            if req is None:
                partes.append("<b>%s</b>: sem seleção" % _esc(lyr.name()))
                continue
            req.setLimit(1)
            f = next(lyr.getFeatures(req), None)
            if f is None:
                partes.append("<b>%s</b>: sem feições" % _esc(lyr.name()))
                continue
            ctx = contexto(lyr, f)
            e = QgsExpression(texto)
            e.prepare(ctx)
            v = e.evaluate(ctx)
            if e.hasEvalError():
                partes.append("<b>%s</b>: <span style='color:#b00020'>%s</span>" % (_esc(lyr.name()), _esc(e.evalErrorString())))
            else:
                partes.append("<b>%s</b> (fid %s): %s" % (_esc(lyr.name()), f.id(), _esc(_mostrar(v))))
        if len(camadas) > MAX_PREVIA:
            partes.append("... e mais %d camada(s)" % (len(camadas) - MAX_PREVIA))
        self.rotulo_previa.setText("Prévia: " + "  ·  ".join(partes))

    def validar(self, mostrar=False):
        """Devolve (camadas, destino, texto) se dá para executar; senão None (e avisa)."""
        camadas = self.camadas_marcadas()
        destino = self.combo_destino.currentText()
        texto = self.expressao.expression().strip()
        problemas = []
        if not camadas:
            problemas.append("Marque pelo menos uma camada.")
        if not destino:
            problemas.append("Escolha o campo que recebe o resultado (ele precisa existir em todas "
                             "as camadas marcadas e não pode ser só leitura).")
        exp = QgsExpression(texto)
        if not texto:
            problemas.append("Escreva a expressão.")
        elif exp.hasParserError():
            problemas.append("Expressão com erro: " + exp.parserErrorString())
        else:
            for lyr in camadas:
                nomes = {f.name().lower() for f in lyr.fields()}
                faltam = sorted(c for c in exp.referencedColumns()
                                if c and c != TODOS_ATRIBUTOS and c.lower() not in nomes)
                if faltam:
                    problemas.append("A camada \"%s\" não tem o(s) campo(s) usado(s) na expressão: %s"
                                     % (lyr.name(), ", ".join(faltam)))
                if not camada_aceita_edicao(lyr):
                    problemas.append("A camada \"%s\" não permite alterar atributos." % lyr.name())
        if problemas:
            if mostrar:
                QMessageBox.warning(self, TITULO, "\n\n".join(problemas))
            self.status.setText("%d problema(s) para resolver." % len(problemas))
            return None
        if mostrar:
            QMessageBox.information(self, TITULO, "Tudo certo: %d camada(s), campo \"%s\".\n\nA validação "
                                    "confere a sintaxe e os campos; o resultado em cada feição aparece na prévia."
                                    % (len(camadas), destino))
            self.status.setText("Validado.")
        return camadas, destino, texto

    # ------------------------------------------------------------------ execução
    def executar(self):
        ok = self.validar(mostrar=False)
        if ok is None:
            self.validar(mostrar=True)
            return
        camadas, destino, texto = ok
        nomes_reais = next(n for nome, n, _g, _t in self.comuns if nome == destino)
        planos, sem_sel = [], []
        for lyr in camadas:
            req, n = self._feicoes(lyr)
            if req is None:
                sem_sel.append(lyr.name())
                continue
            planos.append((lyr, req, n))
        if not planos:
            QMessageBox.information(self, TITULO, "Nenhuma das camadas marcadas tem feições selecionadas.")
            return
        total = sum(n for _l, _r, n in planos)
        linhas = "\n".join("  • %s: %d feição(ões)" % (l.name(), n) for l, _r, n in planos)
        aviso = ("\n\nFicam de fora (sem seleção): " + ", ".join(sem_sel)) if sem_sel else ""
        resp = QMessageBox.question(
            self, "Confirmar",
            "Campo: %s\nExpressão: %s\n\n%s\n\nTotal: %d feição(ões).%s\n\nAs alterações ficam na edição "
            "de cada camada. Continuar?" % (destino, texto if len(texto) < 200 else texto[:200] + "…",
                                             linhas, total, aviso),
            SIM | NAO, NAO)
        if resp != SIM:
            return

        progresso = QProgressDialog("Rodando a expressão…", "Cancelar", 0, max(total, 1), self)
        progresso.setWindowModality(MODAL_JANELA)
        progresso.setMinimumDuration(0)
        feitos, resumo, erros = 0, [], []
        cancelado = False
        try:
            for lyr, req, n in planos:
                if progresso.wasCanceled():
                    break
                alteradas, feitos = self._rodar_camada(lyr, req, nomes_reais[lyr.id()], texto, progresso,
                                                       feitos, erros)
                resumo.append("  • %s: %s" % (lyr.name(), alteradas if isinstance(alteradas, str)
                                                else "%d alterada(s) de %d" % (alteradas, n)))
            cancelado = progresso.wasCanceled()     # antes de fechar: fechar conta como "cancelar" no Qt
        finally:
            progresso.close()
        texto_final = ("Cancelado no meio: as camadas que já tinham rodado ficaram alteradas.\n\n"
                       if cancelado else "") + \
            "Campo \"%s\":\n%s" % (destino, "\n".join(resumo))
        if sem_sel:
            texto_final += "\n\nFicaram de fora (sem seleção): " + ", ".join(sem_sel)
        if erros:
            texto_final += "\n\nErros (%d), primeiros:\n%s" % (len(erros), "\n".join(erros[:12]))
        texto_final += ("\n\nAs alterações estão na edição de cada camada: confira e use Salvar Edições "
                        "ou desfaça com Ctrl+Z (um por camada).")
        QMessageBox.information(self, TITULO, texto_final)
        self.status.setText("Concluído: %d camada(s), %d erro(s)." % (len(resumo), len(erros)))
        self._atualizar_textos()

    def _rodar_camada(self, lyr, req, campo, texto, progresso, feitos, erros):
        """Roda a expressão numa camada. Devolve (nº de feições alteradas ou texto do problema, feitos)."""
        idx = lyr.fields().lookupField(campo)
        if not lyr.isEditable() and not lyr.startEditing():
            erros.append("%s: não foi possível colocar em edição." % lyr.name())
            return "não entrou em edição", feitos
        fld = lyr.fields()[idx]
        ctx = contexto(lyr)
        exp = QgsExpression(texto)
        exp.prepare(ctx)
        if not exp.needsGeometry():
            req.setFlags(req.flags() | _sem_geometria())
        alteradas = 0
        lyr.beginEditCommand("%s (%s)" % (TITULO, campo))
        try:
            for f in lyr.getFeatures(req):
                if progresso.wasCanceled():
                    break
                feitos += 1
                if feitos % 50 == 0:
                    progresso.setValue(feitos)
                    progresso.setLabelText("%s: %d…" % (lyr.name(), feitos))
                ctx.setFeature(f)
                v = exp.evaluate(ctx)
                if exp.hasEvalError():
                    erros.append("%s, fid %s: %s" % (lyr.name(), f.id(), exp.evalErrorString()))
                    continue
                try:
                    v = fld.convertCompatible(v)
                except ValueError as err:
                    erros.append("%s, fid %s: %s" % (lyr.name(), f.id(), err))
                    continue
                antigo = f.attribute(idx)
                if iguais(antigo, v):
                    continue
                if lyr.changeAttributeValue(f.id(), idx, v, antigo):
                    alteradas += 1
                else:
                    erros.append("%s, fid %s: a camada recusou o valor." % (lyr.name(), f.id()))
        except Exception:
            lyr.destroyEditCommand()
            raise
        if alteradas:
            lyr.endEditCommand()
        else:
            lyr.destroyEditCommand()
        lyr.triggerRepaint()
        progresso.setValue(feitos)
        return alteradas, feitos


def _rich():
    from qgis.PyQt.QtCore import Qt
    from ..guias.compat import _pegar
    return _pegar(Qt, "TextFormat.RichText", "RichText")


def _esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mostrar(v):
    from .comum import nulo
    if nulo(v):
        return "NULL"
    if isinstance(v, str):
        return "'%s'" % (v if len(v) <= 60 else v[:59] + "…")
    return str(v)
