"""Teste do 'Calcular Testadas pelo Logradouro do Lote (Faces Colineares)'.

Rodar na raiz do repositório (fica fora do zip do complemento):
    "C:\\Program Files\\QGIS 3.44.12\\bin\\python-qgis-ltr.bat" testes\\teste_testada_faces_colineares.py
"""
import math, os, sys
from qgis.core import QgsApplication, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY as P

app = QgsApplication([], False)
app.initQgis()
sys.path.append(os.path.join(QgsApplication.prefixPath(), 'python', 'plugins'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'codigo'))
from processing.core.Processing import Processing
Processing.initialize()
import processing
from ferramentas_solo.processamento.provider import ProvedorSolo

provedor = ProvedorSolo()  # guardar a referência, senão o Python descarta o provedor
QgsApplication.processingRegistry().addProvider(provedor)

vias = QgsVectorLayer('LineString?crs=EPSG:31983&field=Nome:string', 'vias', 'memory')
lotes = QgsVectorLayer('Polygon?crs=EPSG:31983&field=id:string&field=logradouro:string', 'lotes', 'memory')

def add(layer, geom, attrs):
    f = QgsFeature(layer.fields()); f.setGeometry(geom); f.setAttributes(attrs)
    layer.dataProvider().addFeature(f)

add(vias, QgsGeometry.fromPolylineXY([P(-10, 0), P(60, 0)]), ['Rua A'])
add(vias, QgsGeometry.fromPolylineXY([P(55, -10), P(55, 40)]), ['Rua B'])
cx, cy, r = 0, -200, 100
ponto = lambda raio, a: P(cx + raio * math.cos(math.radians(a)), cy + raio * math.sin(math.radians(a)))
add(vias, QgsGeometry.fromPolylineXY([ponto(r, a) for a in range(60, 121, 2)]), ['RUA CURVA'])

esperado = {}
def lote(id_, pts, logr, exp):
    add(lotes, QgsGeometry.fromPolygonXY([pts + [pts[0]]]), [id_, logr])
    esperado[id_] = exp

lote('vertices_extras', [P(0, 5), P(3, 5), P(7, 5), P(10, 5), P(10, 25), P(0, 25)], 'RUA A', 10)
lote('face_curta', [P(10, 5), P(14.95, 5), P(15.05, 5), P(20, 5), P(20, 25), P(10, 25)], 'Rua A', 10)
lote('em_L_atras_do_vizinho', [P(20, 5), P(25, 5), P(25, 15), P(30, 15), P(30, 25), P(20, 25)], 'Rua A', 5)
lote('vizinho', [P(25, 5), P(30, 5), P(30, 15), P(25, 15)], 'Rua A', 5)
lote('raso_fundo_perto', [P(30, 5), P(40, 5), P(40, 12), P(30, 12)], 'Rua A', 10)
lote('esquina_chanfro', [P(40, 5), P(48, 5), P(50, 7), P(50, 25), P(40, 25)], 'Rua A', 8)
frente = [ponto(r - 6, a) for a in (90, 88, 86, 84, 82, 80)]
exp_curva = sum(frente[i].distance(frente[i + 1]) for i in range(5))
lote('curva', frente + [ponto(r - 26, 80), ponto(r - 26, 90)], 'Rua Curva', round(exp_curva, 2))

res = processing.run('ferramentas_solo:calcular_testadas_logradouro_colineares', {
    'INPUT_LOTES': lotes, 'INPUT_VIAS': vias, 'TOLERANCIA': 15.0, 'ANGULO_MAX': 20.0,
    'OUTPUT': 'memory:', 'OUTPUT_LINHAS': 'memory:'})
saida, linhas = res['OUTPUT'], res['OUTPUT_LINHAS']

obtido = {f['id']: f['testada'] for f in saida.getFeatures()}
for k, v in esperado.items():
    assert abs(obtido[k] - v) < 0.02, f'{k}: obtido {obtido[k]}, esperado {v}'
assert linhas.featureCount() == len(esperado)
assert all(f.geometry().constGet().numGeometries() == 1 for f in linhas.getFeatures()), 'testada partida'
print('OK -', len(esperado), 'lotes conferidos')
app.exitQgis()
