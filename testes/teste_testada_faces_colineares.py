"""Teste do 'Calcular Testadas por Via (Faces Colineares e Esquinas)'.

Rodar na raiz do repositório (fica fora do zip do complemento):
    "C:\\Program Files\\QGIS 3.44.12\\bin\\python-qgis-ltr.bat" testes\\teste_testada_faces_colineares.py
"""
import math, os, sys
from qgis.core import QgsApplication, QgsVectorLayer, QgsFeature, QgsGeometry, QgsWkbTypes, QgsPointXY as P

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

vias = QgsVectorLayer('LineString?crs=EPSG:31983&field=Nome:string&field=cod:string', 'vias', 'memory')
lotes = QgsVectorLayer('Polygon?crs=EPSG:31983&field=id:string&field=uuid:string', 'lotes', 'memory')

def add(layer, geom, attrs):
    f = QgsFeature(layer.fields()); f.setGeometry(geom); f.setAttributes(attrs)
    layer.dataProvider().addFeature(f)

def via(nome, cod, *pts):
    add(vias, QgsGeometry.fromPolylineXY([P(*p) for p in pts]), [nome, cod])

via('Rua A', 'A', (-10, 0), (60, 0))
via('Rua B', 'B', (55, -10), (55, 40))
cx, cy, r = 0, -200, 100
ponto = lambda raio, a: P(cx + raio * math.cos(math.radians(a)), cy + raio * math.sin(math.radians(a)))
add(vias, QgsGeometry.fromPolylineXY([ponto(r, a) for a in range(60, 121, 2)]), ['RUA CURVA', 'CURVA'])
via('Rua Sem Saida', 'SS', (100, -40), (100, 0))
via('Rua C', 'C', (200, 100), (260, 100))
via('Rua D', 'D', (0, 300), (40, 300))
via('Rua E', 'E', (0, 330), (40, 330))
via('Rua F', 'F1', (400, 0), (410, 0))                      # mesma rua, eixo partido em dois trechos
via('Rua F', 'F2', (410, 0), (440, 0))
via('SEM DENOMINAÇÃO OFICIAL', 'G1', (600, 500), (720, 500))  # duas ruas diferentes, mesmo nome
via('SEM DENOMINAÇÃO OFICIAL', 'G2', (700, 420), (700, 600))
via(None, 'H1', (800, 0), (840, 0))                          # duas ruas paralelas sem nome
via(None, 'H2', (800, 30), (840, 30))
via('SEM DENOMINAÇÃO OFICIAL', 'J1', (900, 0), (940, 0))     # duas ruas paralelas com o mesmo nome
via('SEM DENOMINAÇÃO OFICIAL', 'J2', (900, 30), (940, 30))
via('Rua K', 'K', (1000, 0), (1040, 0))

esperado = {}  # id -> ([testada_1, testada_2, ...], precisão)
def lote(id_, pts, testadas, precisao):
    add(lotes, QgsGeometry.fromPolygonXY([[P(*p) for p in pts + [pts[0]]]]), [id_, '{uuid-%s}' % id_])
    esperado[id_] = (testadas, precisao)

lote('vertices_extras', [(0, 5), (3, 5), (7, 5), (10, 5), (10, 25), (0, 25)], [10], 'ALTA')
lote('face_curta', [(10, 5), (14.95, 5), (15.05, 5), (20, 5), (20, 25), (10, 25)], [10], 'ALTA')
lote('em_L_atras_do_vizinho', [(20, 5), (25, 5), (25, 15), (30, 15), (30, 25), (20, 25)], [5], 'ALTA')
lote('vizinho', [(25, 5), (30, 5), (30, 15), (25, 15)], [5], 'ALTA')
lote('raso_fundo_perto', [(30, 5), (40, 5), (40, 12), (30, 12)], [10], 'ALTA')
# esquina com chanfro: Rua B (18 m) e Rua A (8 m); o chanfro fica de fora
lote('esquina_chanfro', [(40, 5), (48, 5), (50, 7), (50, 25), (40, 25)], [18, 8], 'ALTA')
frente = [ponto(r - 6, a) for a in (90, 88, 86, 84, 82, 80)]
exp_curva = round(sum(frente[i].distance(frente[i + 1]) for i in range(5)), 2)
lote('curva', [(p.x(), p.y()) for p in frente + [ponto(r - 26, 80), ponto(r - 26, 90)]], [exp_curva], 'ALTA')
# lote no fim da rua: a frente é perpendicular ao eixo -> face mais próxima
lote('fim_da_rua', [(95, 4), (105, 4), (105, 24), (95, 24)], [10], 'MÉDIA')
lote('perto_so_da_rua_a', [(-9, 5), (-1, 5), (-1, 25), (-9, 25)], [8], 'ALTA')
# na ponta da Rua A e ao lado da Rua B
lote('ponta_da_rua_a', [(58, 10), (68, 10), (68, 30), (58, 30)], [20, 10], 'ALTA')
lote('longe_de_tudo', [(0, 30), (10, 30), (10, 50), (0, 50)], [], 'SEM TESTADA')
# lote atrás de outro polígono, a 18 m do eixo: não tem frente
lote('poligono_na_frente', [(200, 103), (220, 103), (220, 118), (200, 118)], [20], 'ALTA')
lote('atras_do_poligono', [(200, 118), (220, 118), (220, 138), (200, 138)], [], 'SEM TESTADA')
# lote que vai de uma rua a outra (caso do print)
lote('duas_frentes', [(10, 305), (20, 305), (20, 325), (10, 325)], [10, 10], 'ALTA')
# frente sobre os dois trechos do eixo partido: uma testada só
lote('eixo_partido', [(405, 5), (410, 5), (425, 5), (425, 25), (405, 25)], [20], 'ALTA')
# esquina de duas ruas com o mesmo nome genérico: duas testadas
lote('esquina_mesmo_nome', [(680, 505), (695, 505), (695, 525), (680, 525)], [20, 15], 'ALTA')
# lote que vai de uma rua a outra, as duas sem nome / com o mesmo nome: duas testadas, não uma de 20 m
lote('duas_frentes_sem_nome', [(810, 5), (820, 5), (820, 25), (810, 25)], [10, 10], 'ALTA')
lote('duas_frentes_mesmo_nome', [(910, 5), (920, 5), (920, 25), (910, 25)], [10, 10], 'ALTA')
# frente com degrau de 1 m: testada de 4 + 6 m, em duas partes (o degrau não conta)
lote('frente_degrau', [(1010, 5), (1014, 5), (1014, 6), (1020, 6), (1020, 25), (1010, 25)], [10], 'ALTA')

def rodar(**extra):
    return processing.run('ferramentas_solo:calcular_testadas_logradouro_colineares', dict({
        'INPUT_LOTES': lotes, 'INPUT_VIAS': vias, 'TOLERANCIA': 15.0, 'ANGULO_MAX': 20.0,
        'OUTPUT': 'memory:', 'OUTPUT_LINHAS': 'memory:'}, **extra))

res = rodar(CAMPO_COD_VIA='cod')
saida, linhas = res['OUTPUT'], res['OUTPUT_LINHAS']
assert 'via_cod' not in rodar()['OUTPUT_LINHAS'].fields().names()  # sem o parâmetro, sem o campo

campos_testada = [n for n in saida.fields().names() if n.startswith('testada_')]
assert campos_testada == ['testada_1', 'testada_2'], campos_testada
for f in saida.getFeatures():
    obtido = [f[c] for c in campos_testada if f[c] is not None and str(f[c]) != 'NULL']
    testadas, precisao = esperado[f['id']]
    ok = len(obtido) == len(testadas) and all(abs(a - b) < 0.02 for a, b in zip(obtido, testadas))
    assert ok and f['precisao_testada'] == precisao, f"{f['id']}: obtido {obtido} {f['precisao_testada']}, esperado {testadas} {precisao}"

uuid_por_id = {f['id']: f['uuid'] for f in lotes.getFeatures()}
por_uuid = {}
for f in linhas.getFeatures():
    por_uuid.setdefault(f['lote_uuid'], []).append((f['testada_num'], f['comprimento'], f['via'], f['via_cod']))
    partes = f.geometry().constGet().numGeometries()
    assert partes == (2 if f['lote_uuid'] == '{uuid-frente_degrau}' else 1), ('testada partida', f['lote_uuid'], partes)
for id_, (testadas, _) in esperado.items():
    obtido = sorted(por_uuid.get(uuid_por_id[id_], []))
    assert [o[0] for o in obtido] == list(range(1, len(testadas) + 1)), (id_, obtido)
    assert all(abs(o[1] - t) < 0.02 for o, t in zip(obtido, testadas)), (id_, obtido)
assert [o[2:] for o in sorted(por_uuid['{uuid-esquina_chanfro}'])] == [('Rua B', 'B'), ('Rua A', 'A')]
assert sorted(o[3] for o in por_uuid['{uuid-duas_frentes}']) == ['D', 'E']
assert [o[3] for o in por_uuid['{uuid-eixo_partido}']] == ['F2']  # trecho com 15 dos 20 m
assert sorted(o[3] for o in por_uuid['{uuid-duas_frentes_sem_nome}']) == ['H1', 'H2']
assert sorted(o[3] for o in por_uuid['{uuid-duas_frentes_mesmo_nome}']) == ['J1', 'J2']
# linhas simples: uma feição por parte, cada uma com o próprio comprimento
lin = rodar(TIPO_LINHA=1)['OUTPUT_LINHAS']
assert lin.wkbType() == QgsWkbTypes.LineString, lin.wkbType()
degrau = sorted((f['testada_num'], f['comprimento']) for f in lin.getFeatures() if f['lote_uuid'] == '{uuid-frente_degrau}')
assert degrau == [(1, 4.0), (1, 6.0)], degrau
assert lin.featureCount() == linhas.featureCount() + 1

# somente a testada principal
res = rodar(SOMENTE_PRINCIPAL=True)
campos = [n for n in res['OUTPUT'].fields().names() if n.startswith('testada_')]
assert campos == ['testada_1'], campos
t1 = {f['id']: f['testada_1'] for f in res['OUTPUT'].getFeatures()}
assert t1['esquina_chanfro'] == 18 and t1['ponta_da_rua_a'] == 20 and t1['duas_frentes'] == 10
assert {f['testada_num'] for f in res['OUTPUT_LINHAS'].getFeatures()} == {1}
assert res['OUTPUT_LINHAS'].featureCount() == sum(1 for t, _ in esperado.values() if t)
print('OK -', len(esperado), 'lotes conferidos (multiparte, simples e só a principal)')
app.exitQgis()
