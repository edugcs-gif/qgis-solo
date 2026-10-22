# -*- coding: utf-8 -*-
def classFactory(iface):
    from .plugin import FerramentasSolo
    return FerramentasSolo(iface)
