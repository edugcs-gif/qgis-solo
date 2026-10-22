# -*- coding: utf-8 -*-
"""Utilidades comuns dos algoritmos do Ferramentas Solo."""
from qgis.core import Qgis, QgsProcessingAlgorithm


def sem_thread(flags):
    """Roda na thread principal: obrigatório para algoritmos que editam ou selecionam
    feições de camadas do projeto (evita travamento do QGIS)."""
    try:
        return flags | Qgis.ProcessingAlgorithmFlag.NoThreading      # QGIS >= 3.36
    except AttributeError:
        return flags | QgsProcessingAlgorithm.FlagNoThreading         # QGIS < 3.36
