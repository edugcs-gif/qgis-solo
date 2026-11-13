"""Testes das correções de perda de dados (auditoria de 13/11/2026).

Append, cancelamento, Trocar parte de caminho, teclas da ferramenta de vértice,
Beiral em camada simples, Remover Filetes e Exportar seleção para DXF.

Rodar na raiz do repositório (fica fora do zip do complemento):
    "C:\\Program Files\\QGIS 3.44.12\\bin\\python-qgis-ltr.bat" testes\\teste_correcoes_dados.py
"""
import os, random, sys, tempfile
from unittest import mock
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent
from qgis.PyQt.QtWidgets import QAction, QApplication
from qgis.core import (QgsApplication, QgsFeature, QgsGeometry, QgsProcessingFeedback, QgsProject,
                       QgsRectangle, QgsVectorLayer)
from qgis.gui import QgsMapCanvas

app = QgsApplication([], True)
app.initQgis()
sys.path.append(os.path.join(QgsApplication.prefixPath(), 'python', 'plugins'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'codigo'))
from processing.core.Processing import Processing
Processing.initialize()
import processing
from ferramentas_solo.processamento.provider import ProvedorSolo

provedor = ProvedorSolo()  # guardar a referência, senão o Python descarta o provedor
QgsApplication.processingRegistry().addProvider(provedor)


def camada(nome, tipo='Polygon', campos='&field=k:string&field=v:string'):
    return QgsVectorLayer(f'{tipo}?crs=EPSG:31983{campos}', nome, 'memory')


def add(lyr, linhas):
    fs = []
    for attrs, wkt in linhas:
        f = QgsFeature(lyr.fields()); f.setAttributes(attrs); f.setGeometry(QgsGeometry.fromWkt(wkt)); fs.append(f)
    assert lyr.dataProvider().addFeatures(fs)[0]


def quadrado(i, lado=10):
    x = 350000 + i * 20
    return f'Polygon(({x} 7400000,{x + lado} 7400000,{x + lado} 7400010,{x} 7400010,{x} 7400000))'


# ---- 1. Append não recorta a geometria nova pela antiga ("evitar sobreposição")
alvo, origem = camada('alvo'), camada('origem')
add(alvo, [(['a', 'velho'], 'Polygon((0 0,10 0,10 10,0 10,0 0))')])
add(origem, [(['a', 'novo'], 'Polygon((0 0,10 0,10 12,0 12,0 0))')])
QgsProject.instance().addMapLayers([alvo, origem])
QgsProject.instance().setAvoidIntersectionsLayers([alvo])
processing.run('ferramentas_solo:appendfeaturestolayer', {
    'SOURCE_LAYER': origem, 'SOURCE_FIELD': 'k', 'TARGET_LAYER': alvo, 'TARGET_FIELD': 'k', 'ACTION_ON_DUPLICATE': 2})
QgsProject.instance().setAvoidIntersectionsLayers([])
f = next(alvo.getFeatures())
assert f['v'] == 'novo' and abs(f.geometry().area() - 120) < 1e-6, (f['v'], f.geometry().asWkt())


# ---- 2. Cancelar não grava nada pela metade
def cancelando():
    fb = QgsProcessingFeedback()
    fb.progressChanged.connect(lambda p: fb.cancel() if p >= 50 else None)
    return fb


alvo, origem = camada('alvo2'), camada('origem2')
add(origem, [([f's{i}', 'y'], quadrado(i)) for i in range(200)])
processing.run('ferramentas_solo:appendfeaturestolayer',
               {'SOURCE_LAYER': origem, 'TARGET_LAYER': alvo, 'ACTION_ON_DUPLICATE': 0}, feedback=cancelando())
assert alvo.dataProvider().featureCount() == 0 and not alvo.isEditable(), alvo.dataProvider().featureCount()

# ---- 3. Trocar parte de caminho: deixa pendente na camada, nada direto na fonte; cancelado não muda nada
fotos = camada('fotos', 'None', '&field=caminho:string')
add(fotos, [([r'\\192.168.1.150\D\Imagens_360\f%d.jpg' % i], '') for i in range(200)])
QgsProject.instance().addMapLayer(fotos)
params = {'INPUT': fotos, 'CAMPO': 'caminho', 'TEXTO_ANTIGO': r'\\192.168.1.150\D\Imagens_360',
          'TEXTO_NOVO': r'\\solonas\Imagens_360', 'ATUALIZAR_ORIGINAL': True}
processing.run('ferramentas_solo:trocar_parte_caminho', params, feedback=cancelando())
assert not fotos.isEditable() and all('192.168' in f['caminho'] for f in fotos.getFeatures())
processing.run('ferramentas_solo:trocar_parte_caminho', params)
assert fotos.isEditable() and fotos.undoStack().count() == 1
assert all(f['caminho'].startswith(r'\\solonas') for f in fotos.getFeatures())
assert all('192.168' in f['caminho'] for f in fotos.dataProvider().getFeatures()), 'nada pode ter ido para a fonte'

# ---- 4. Delete na ferramenta de vértice é "usado" pela ferramenta; setas continuam movendo o mapa
from ferramentas_solo.guias.ferramentas import FerramentaDesenhar, FerramentaMoverVertice

canvas = QgsMapCanvas(); canvas.resize(400, 300); canvas.setExtent(QgsRectangle(0, 0, 400, 300))
iface = mock.MagicMock(); iface.mapCanvas.return_value = canvas; iface.mainWindow.return_value = None
for Classe in (FerramentaMoverVertice, FerramentaDesenhar):
    ferramenta = Classe(iface, QAction(None), mock.MagicMock(), mock.MagicMock(), mock.MagicMock())
    canvas.setMapTool(ferramenta)
    padrao = []                                     # teclas que o QGIS recebeu para aplicar o padrão
    canvas.keyPressed.connect(lambda e, p=padrao: p.append(e.key()))
    for tecla in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Escape):
        QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, tecla, Qt.NoModifier))
    assert padrao == [], (Classe.__name__, padrao)
    x0 = canvas.extent().xMinimum()
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier))
    assert canvas.extent().xMinimum() != x0, Classe.__name__ + ': a seta tinha que mover o mapa'
    canvas.keyPressed.disconnect()
    canvas.unsetMapTool(ferramenta)

# ---- 7. Beiral: edificação em H dividida pelo recuo, em camada simples, é recusada (não perde um bloco)
from ferramentas_solo.beiral.plugin import DescontoBeiralPlugin
from ferramentas_solo.beiral.beiral_core import ErroBeiral

H = ('Polygon((110 110, 115 110, 115 112, 116 112, 116 110, 121 110, 121 115, '
     '116 115, 116 113, 115 113, 115 115, 110 115, 110 110))')
lotes = camada('Lotes', campos='')
add(lotes, [([], 'Polygon((100 100, 140 100, 140 140, 100 140, 100 100))')])
for tipo, wkt in (('Polygon', H), ('MultiPolygon', 'Multi' + H.replace('((', '(((') + ')')):
    edif = camada('1 pav', tipo, '&field=uuid:string')
    add(edif, [(['u1'], wkt)])
    try:
        orig, nova, _ = DescontoBeiralPlugin(None)._descontar_feicao(next(edif.getFeatures()), edif, lotes, None)
    except ErroBeiral:
        assert tipo == 'Polygon'
        continue
    assert tipo == 'MultiPolygon' and abs(nova.area() - 2 * 3.8 * 3.8) < 0.01, (tipo, nova.area())

# ---- 8 e 9. Remover Filetes: SRC geográfico não apaga nada; lotes sem filete não são regravados
from ferramentas_solo.filetes import plugin as F

F.DialogoFiletes.exec_ = F.DialogoFiletes.exec = lambda self: F.ACEITO   # "OK" com as opções padrão
filetes = F.RemoverFiletes(iface)

geo = QgsVectorLayer('Polygon?crs=EPSG:4674', 'geo', 'memory')
add(geo, [([], 'Polygon((-47 -23, -46.9999 -23, -46.9999 -22.9998, -47 -22.9998, -47 -23))')])
iface.activeLayer.return_value = geo
filetes.run()
assert not geo.isEditable() and geo.featureCount() == 1

utm = camada('utm', campos='')
random.seed(1)          # lotes tortos com coordenadas quebradas: a área sai com ruído de ~1e-8 m²
for i in range(50):
    x, y = 350000 + i * 30 + random.random(), 7400000 + random.random()
    w, h = 8 + random.random() * 4, 15 + random.random() * 10
    add(utm, [([], f'Polygon(({x} {y},{x + w} {y + 0.37},{x + w - 0.2} {y + h},{x - 0.1} {y + h - 0.3},{x} {y}))')])
iface.activeLayer.return_value = utm
filetes.run()
assert not utm.editBuffer().changedGeometries(), 'lote sem filete não pode virar edição pendente'

# ---- 10. Exportar seleção para DXF com camada de linha não zera a exportação anterior
from ferramentas_solo.dxf import exportador

linhas = camada('linhas', 'LineString', '')
add(linhas, [([], 'LineString(0 0, 10 10)')])
linhas.selectAll()
iface.activeLayer.return_value = linhas
saida = os.path.join(tempfile.mkdtemp(), 'Exportado(DXF).dxf')
with open(saida, 'w') as arq:
    arq.write('exportação anterior')
with mock.patch.object(exportador, 'OUTPUT_FILE', saida):
    exportador.exportar_selecao(iface)
with open(saida) as arq:
    assert arq.read() == 'exportação anterior'

print('OK')
