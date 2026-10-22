# -*- coding: utf-8 -*-
"""Intervalo de distância (aderência por passo) das ferramentas de desenhar e mover vértice.

Com o intervalo ligado (ex.: 0,50 m), o lado que está sendo desenhado ou editado só
assume comprimentos múltiplos do intervalo:
  - desenhar: distância do último ponto até o cursor (mantém a direção do cursor ou a guia);
  - mover vértice: comprimento do lado da guia em que o vértice desliza; livre, o lado anterior;
  - ponto: deslocamento a partir da posição original.
Vértice, interseção e borda continuam tendo prioridade; SHIFT deixa tudo livre.

Botão na barra: clique liga/desliga, a seta escolhe o intervalo ("Definir intervalo..." ou
valores prontos). Tecla D dentro das ferramentas também liga/desliga. Tudo fica salvo.
"""
import os
from functools import partial

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QInputDialog, QMenu, QToolButton
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6
from qgis.core import QgsSettings

CHAVE_LIGADO = "FerramentasSolo/intervalo_ligado"
CHAVE_VALOR = "FerramentasSolo/intervalo_valor"
TITULO = "Intervalo de distância (aderência)"
ICONE = os.path.join(os.path.dirname(__file__), "icons", "intervalo.svg")
PRONTOS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
MINIMO, MAXIMO = 0.001, 100000.0


def _popup():
    try:
        return QToolButton.ToolButtonPopupMode.MenuButtonPopup
    except AttributeError:
        return QToolButton.MenuButtonPopup


class IntervaloDistancia:

    def __init__(self, iface, opcoes=None):
        self.iface = iface
        self.opcoes = opcoes            # Comprimento e Área (para usar a mesma vírgula decimal)
        s = QgsSettings()
        self.ligado = s.value(CHAVE_LIGADO, False, type=bool)
        try:
            self.valor = float(s.value(CHAVE_VALOR, 0.5))
        except (TypeError, ValueError):
            self.valor = 0.5
        if not (MINIMO <= self.valor <= MAXIMO):
            self.valor = 0.5
        self.acao = None
        self.menu = None
        self._prontos = {}

    # ---------- texto ----------
    def texto(self, valor=None):
        v = self.valor if valor is None else valor
        casas = 2
        while casas < 3 and abs(round(v, casas) - v) > 1e-9:
            casas += 1
        s = "{:.{c}f}".format(v, c=casas)
        virgula = True if self.opcoes is None else getattr(self.opcoes, "comma", True)
        return s.replace(".", ",") if virgula else s

    # ---------- botão ----------
    def criar_acao(self, parent):
        self.acao = QAction(QIcon(ICONE), TITULO, parent)
        self.acao.setObjectName("mFerramentasSoloIntervalo")
        self.acao.setCheckable(True)
        self.acao.setChecked(self.ligado)
        self.acao.toggled.connect(self._ao_alternar)

        self.menu = QMenu("Intervalo de distância", parent)
        a = self.menu.addAction("Definir intervalo...")
        a.triggered.connect(self.perguntar)
        self.menu.addSeparator()
        for v in PRONTOS:
            a = self.menu.addAction(self.texto(v) + " m")
            a.setCheckable(True)
            a.triggered.connect(partial(self.definir, v))
            self._prontos[v] = a
        self._atualizar()
        return self.acao

    def configurar_botao(self, toolbar):
        """Clique = liga/desliga, seta = escolher o intervalo (chamar depois de pôr na barra)."""
        botao = toolbar.widgetForAction(self.acao)
        if isinstance(botao, QToolButton):
            botao.setMenu(self.menu)
            botao.setPopupMode(_popup())

    def _atualizar(self):
        for v, a in self._prontos.items():
            a.setChecked(abs(v - self.valor) < 1e-9)
        if self.acao is not None:
            self.acao.setToolTip(
                "%s — %s (%s m)\nO lado desenhado/editado só assume comprimentos múltiplos do intervalo.\n"
                "Clique liga/desliga · seta escolhe o intervalo · tecla D dentro das ferramentas.\n"
                "SHIFT desenha livre; vértice, interseção e borda têm prioridade."
                % (TITULO, "Ligado" if self.ligado else "Desligado", self.texto()))

    def _ao_alternar(self, marcado):
        self.ligado = bool(marcado)
        QgsSettings().setValue(CHAVE_LIGADO, self.ligado)
        self._atualizar()

    def alternar(self):
        """Tecla D: inverte e devolve o novo estado."""
        if self.acao is not None:
            self.acao.setChecked(not self.ligado)
        else:
            self._ao_alternar(not self.ligado)
        return self.ligado

    def definir(self, valor, *_):
        valor = float(valor)
        if not (MINIMO <= valor <= MAXIMO):
            return
        self.valor = valor
        QgsSettings().setValue(CHAVE_VALOR, valor)
        if self.acao is not None and not self.ligado:
            self.acao.setChecked(True)      # escolher um intervalo já liga
        else:
            self._atualizar()

    def perguntar(self):
        valor, ok = QInputDialog.getDouble(
            self.iface.mainWindow(), "Intervalo de distância",
            "Intervalo (m) — o lado só assume múltiplos deste valor:",
            self.valor, MINIMO, MAXIMO, 3)
        if ok:
            self.definir(valor)

    def remover(self):
        if self.menu is not None:
            self.menu.deleteLater()
            self.menu = None
        if self.acao is not None:
            self.acao.deleteLater()
            self.acao = None
