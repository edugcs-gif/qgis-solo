# -*- coding: utf-8 -*-
import sys


def _limpar_modulos_antigos():
    """Ao atualizar o complemento pelo gerenciador (sem reiniciar o QGIS), partes da versão
    anterior podiam continuar na memória do Python e alguns botões seguiam com o código antigo.
    Aqui todo submódulo já carregado deste complemento é descartado, para tudo ser lido de novo
    dos arquivos instalados."""
    prefixo = __name__ + "."
    for nome in [n for n in sys.modules if n.startswith(prefixo)]:
        sys.modules.pop(nome, None)


def classFactory(iface):
    _limpar_modulos_antigos()
    from .plugin import FerramentasSolo
    return FerramentasSolo(iface)
