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
add(vias, QgsGeometry.fromPolylineXY([P(100, -40), P(100, 0)]), ['Rua Sem Saida'])
add(vias, QgsGeometry.fromPolylineXY([P(200, 100), P(260, 100)]), ['Rua C'])

esperado = {}  # id -> (testada, precisão)
def lote(id_, pts, logr, testada, precisao):
    add(lotes, QgsGeometry.fromPolygonXY([pts + [pts[0]]]), [id_, logr])
    esperado[id_] = (testada, precisao)

lote('vertices_extras', [P(0, 5), P(3, 5), P(7, 5), P(10, 5), P(10, 25), P(0, 25)], 'RUA A', 10, 'ALTA')
lote('face_curta', [P(10, 5), P(14.95, 5), P(15.05, 5), P(20, 5), P(20, 25), P(10, 25)], 'Rua A', 10, 'ALTA')
lote('em_L_atras_do_vizinho', [P(20, 5), P(25, 5), P(25, 15), P(30, 15), P(30, 25), P(20, 25)], 'Rua A', 5, 'ALTA')
lote('vizinho', [P(25, 5), P(30, 5), P(30, 15), P(25, 15)], 'Rua A', 5, 'ALTA')
lote('raso_fundo_perto', [P(30, 5), P(40, 5), P(40, 12), P(30, 12)], 'Rua A', 10, 'ALTA')
lote('esquina_chanfro', [P(40, 5), P(48, 5), P(50, 7), P(50, 25), P(40, 25)], 'Rua A', 8, 'ALTA')
frente = [ponto(r - 6, a) for a in (90, 88, 86, 84, 82, 80)]
exp_curva = round(sum(frente[i].distance(frente[i + 1]) for i in range(5)), 2)
lote('curva', frente + [ponto(r - 26, 80), ponto(r - 26, 90)], 'Rua Curva', exp_curva, 'ALTA')
# lote no fim da rua: a frente é perpendicular ao eixo -> face mais próxima
lote('fim_da_rua', [P(95, 4), P(105, 4), P(105, 24), P(95, 24)], 'Rua Sem Saída', 10, 'MÉDIA')
# nome sem eixo (ex.: "SEM DENOMINAÇÃO" x "SEM DENOMINAÇÃO OFICIAL") -> via mais próxima
lote('nome_sem_eixo', [P(-9, 5), P(-1, 5), P(-1, 25), P(-9, 25)], 'Rua Que Nao Existe', 8, 'BAIXA')
# sem logradouro, mas encostado na Rua B -> via mais próxima
lote('sem_logradouro_perto', [P(58, 10), P(68, 10), P(68, 30), P(58, 30)], None, 20, 'BAIXA')
# sem logradouro e longe de qualquer via
lote('sem_logradouro_longe', [P(0, 30), P(10, 30), P(10, 50), P(0, 50)], None, 0, 'SEM TESTADA')
# lote atrás de um polígono sem dados, a 18 m do eixo do logradouro (caso do lote 331)
lote('poligono_sem_dados', [P(200, 103), P(220, 103), P(220, 118), P(200, 118)], None, 20, 'BAIXA')
lote('atras_do_sem_dados', [P(200, 118), P(220, 118), P(220, 138), P(200, 138)], 'Rua C', 20, 'BAIXA')

res = processing.run('ferramentas_solo:calcular_testadas_logradouro_colineares', {
    'INPUT_LOTES': lotes, 'INPUT_VIAS': vias, 'TOLERANCIA': 15.0, 'ANGULO_MAX': 20.0,
    'OUTPUT': 'memory:', 'OUTPUT_LINHAS': 'memory:'})
saida, linhas = res['OUTPUT'], res['OUTPUT_LINHAS']

obtido = {f['id']: (f['testada'], f['precisao_testada'], f['obs_testada']) for f in saida.getFeatures()}
for k, (testada, precisao) in esperado.items():
    t, p, obs = obtido[k]
    assert abs(t - testada) < 0.02 and p == precisao, f'{k}: obtido {t} {p} ({obs}), esperado {testada} {precisao}'
assert 'via mais próxima: Rua A' in obtido['nome_sem_eixo'][2], obtido['nome_sem_eixo']
assert 'lote sem logradouro' in obtido['sem_logradouro_perto'][2], obtido['sem_logradouro_perto']
assert linhas.featureCount() == sum(t > 0 for t, _ in esperado.values())
assert all(f.geometry().constGet().numGeometries() == 1 for f in linhas.getFeatures()), 'testada partida'
print('OK -', len(esperado), 'lotes conferidos')
app.exitQgis()
