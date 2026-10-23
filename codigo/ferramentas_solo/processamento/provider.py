# -*- coding: utf-8 -*-
"""Provedor "Ferramentas Solo" da Caixa de Ferramentas de Processamento.

Todos os scripts de Processamento da Solo ficam juntos aqui, direto sob
"Ferramentas Solo" (sem subgrupos). Para incluir um novo: coloque o .py em
algoritmos/ e acrescente a classe na lista ALGORITMOS abaixo.
"""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

from .algoritmos.comparar_e_selecionar import SelecaoDinamicCamadas
from .algoritmos.corrigir_geometrias import CorrigirGeometriasNaCamada
from .algoritmos.gerar_atualizar_quadras import GerarAtualizarQuadras
from .algoritmos.puxar_campo_intersecao import PuxarCampoIntersecao
from .algoritmos.puxar_valor_por_chave import TransferirValoresTabelasECamadas
from .algoritmos.selecionar_unidades import SelecionarUnidadesInconsistentes
from .algoritmos.testada_logradouro import CalculadoraTestadasQGIS as TestadaLogradouro
from .algoritmos.testada_via_proxima import CalculadoraTestadasQGIS as TestadaViaProxima
from .algoritmos.trocar_parte_caminho import TrocarParteCaminho

ALGORITMOS = (
    TestadaViaProxima,
    TestadaLogradouro,
    GerarAtualizarQuadras,
    PuxarCampoIntersecao,
    TransferirValoresTabelasECamadas,
    SelecaoDinamicCamadas,
    SelecionarUnidadesInconsistentes,
    TrocarParteCaminho,
    CorrigirGeometriasNaCamada,
)

ICONE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.svg")


class ProvedorSolo(QgsProcessingProvider):

    def id(self):
        return "ferramentas_solo"

    def name(self):
        return "Ferramentas Solo"

    def longName(self):
        return "Ferramentas Solo — Solo Topografia e Georreferenciamento"

    def icon(self):
        return QIcon(ICONE)

    def loadAlgorithms(self):
        for Classe in ALGORITMOS:
            self.addAlgorithm(Classe())
