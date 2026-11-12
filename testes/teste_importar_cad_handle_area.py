"""Teste do Importar CAD (DXF): campos Handle (todos os tipos) e Area (polígonos).

Rodar na raiz do repositório (fica fora do zip do complemento):
    "C:\\Program Files\\QGIS 3.44.12\\bin\\python-qgis-ltr.bat" testes\\teste_importar_cad_handle_area.py
"""
import os, sys, tempfile
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject

app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'codigo'))
import ezdxf
from ferramentas_solo.dxf import logic

dxf = os.path.join(tempfile.mkdtemp(), "teste.dxf")
doc = ezdxf.new()
msp = doc.modelspace()
lote = msp.add_lwpolyline([(0, 0), (10, 0), (10, 20), (0, 20)], close=True, dxfattribs={"layer": "LOTE"})
rua = msp.add_line((0, -5), (30, -5), dxfattribs={"layer": "RUA"})
txt = msp.add_text("Lote 01", dxfattribs={"layer": "TEXTO", "insert": (3, 15)})
doc.saveas(dxf)
QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:31982"))


def feicoes(funcao):
    antes = set(QgsProject.instance().mapLayers())
    funcao(dxf, logic.FeedbackSimples(), logic.Destino("memoria"))
    return [f for lid, l in QgsProject.instance().mapLayers().items() if lid not in antes for f in l.getFeatures()]


pol = feicoes(logic.importar_poligonos)
assert [(f["Layer_CAD"], f["Handle"], f["Area"]) for f in pol] == [("LOTE", lote.dxf.handle, 200.0)], pol
assert [f["Handle"] for f in feicoes(logic.importar_linhas)] == [rua.dxf.handle]
assert [f["Handle"] for f in feicoes(logic.importar_textos)] == [txt.dxf.handle]
assert {f["Handle"] for f in feicoes(logic.importar_pontos)} == {lote.dxf.handle}  # vértices levam o Handle da polilinha
print("OK - Handle e Area no Importar CAD")
