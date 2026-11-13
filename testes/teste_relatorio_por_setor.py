"""Teste da aba "Por setor" do Relatório de Produtividade (contagem das feições por setor x valor).

Rodar na raiz do repositório (fica fora do zip do complemento):
    "C:\\Program Files\\QGIS 3.44.12\\bin\\python-qgis-ltr.bat" testes\\teste_relatorio_por_setor.py
"""
import os, sys, tempfile
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')   # a janela do relatório é criada, mas não aparece
from qgis.PyQt.QtCore import QCoreApplication, QSettings, Qt
from qgis.core import (QgsApplication, QgsCoordinateTransform, QgsEditorWidgetSetup, QgsFeature, QgsGeometry,
                       QgsPointXY as P, QgsProject, QgsVectorLayer)

app = QgsApplication([], True)
app.initQgis()
QCoreApplication.setOrganizationName('teste_ferramentas_solo')   # preferências do teste longe das do QGIS
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp())
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'codigo'))
from ferramentas_solo.relatorio import exportar
from ferramentas_solo.relatorio.plugin import DialogoRelatorio, Setores


def add(layer, geom, attrs):
    f = QgsFeature(layer.fields()); f.setGeometry(geom); f.setAttributes(attrs)
    layer.dataProvider().addFeature(f)


def quadrado(x0, y0, lado):
    return QgsGeometry.fromPolygonXY([[P(x0, y0), P(x0 + lado, y0), P(x0 + lado, y0 + lado), P(x0, y0 + lado), P(x0, y0)]])


# setores "2" e "10" lado a lado (divisa em X + 100), em UTM 23S
X, Y = 500000, 7500000
setores = QgsVectorLayer('Polygon?crs=EPSG:31983&field=setor:string', 'Setores', 'memory')
add(setores, quadrado(X, Y, 100), ['2'])
add(setores, quadrado(X + 100, Y, 100), ['10'])

campos = '&field=user:string&field=created_at:string&field=confirmado:string'
pontos = QgsVectorLayer('Point?crs=EPSG:4326' + campos, 'Geocodificacao', 'memory')   # SRC diferente do dos setores
para_4326 = QgsCoordinateTransform(setores.crs(), pontos.crs(), QgsProject.instance())


def ponto(x, y, status):
    g = QgsGeometry.fromPointXY(P(X + x, Y + y)); g.transform(para_4326)
    add(pontos, g, ['ana', '2026-11-10', status])


ponto(10, 10, 'SIM'); ponto(20, 20, 'SIM'); ponto(30, 30, 'NÃO'); ponto(40, 40, None)   # setor 2
ponto(150, 50, 'SIM'); ponto(160, 50, 'POSSIVELMENTE SIM')                              # setor 10
ponto(500, 500, 'NÃO')                                                                  # fora dos setores

# lote do setor 2 encostado na divisa com o 10: conta só no 2
lotes = QgsVectorLayer('Polygon?crs=EPSG:31983' + campos, 'Lotes', 'memory')
add(lotes, quadrado(X + 80, Y + 10, 20), ['bia', '2026-11-10', 'SIM'])

QgsProject.instance().addMapLayers([setores, pontos, lotes])

# ponto em cima da divisa: cai nos dois setores
assert Setores(setores, 'setor')(QgsGeometry.fromPointXY(P(X + 100, Y + 50))) == ['10', '2']


class Iface:      # só o que a janela usa
    def mainWindow(self):
        return None

    def activeLayer(self):
        return None


dlg = DialogoRelatorio(Iface())
for i in range(dlg.lista.count()):
    it = dlg.lista.item(i)
    it.setCheckState(Qt.Checked if it.text() in ('Geocodificacao', 'Lotes') else Qt.Unchecked)
dlg.cb_user.setCurrentText('user'); dlg.cb_data.setCurrentText('created_at')
dlg.cb_valor.setCurrentText('confirmado')
dlg.chk_preenchido.setChecked(False)

assert dlg.cb_setores.findText('Geocodificacao') < 0, 'só camada de polígono pode ser a de setores'
dlg.cb_setores.setCurrentText('Setores')
assert dlg.cb_campo_setor.currentText() == 'setor', dlg.cb_campo_setor.currentText()

# sem mapa de valores: colunas com o valor do campo, sem percentual; setor 2 antes do 10
dlg.gerar()
assert exportar.cabecalho_setor(dlg.res) == ['Setor', 'NÃO', 'POSSIVELMENTE SIM', 'SIM', '(vazio)', 'Total'], \
    exportar.cabecalho_setor(dlg.res)
assert exportar.linhas_setor(dlg.res) == [
    ['2', 1, 0, 3, 1, 5],
    ['10', 0, 1, 1, 0, 2],
    ['Fora dos setores', 1, 0, 0, 0, 1],
    ['TOTAL', 2, 1, 4, 1, 8]], exportar.linhas_setor(dlg.res)
assert dlg.abas.indexOf(dlg.tb_setor) >= 0 and dlg.tb_setor.item(0, 0).text() == '2'
assert dlg.res['total'] == 8                      # as outras abas continuam contando cada feição uma vez

# usuário x setor
assert exportar.cabecalho_usuario_setor(dlg.res) == ['Usuário', '2', '10', 'Fora dos setores', 'Total']
assert exportar.linhas_usuario_setor(dlg.res) == [
    ['ana', 4, 2, 1, 7],
    ['bia', 1, 0, 0, 1],
    ['TOTAL', 5, 2, 1, 8]], exportar.linhas_usuario_setor(dlg.res)
assert dlg.abas.indexOf(dlg.tb_usuario_setor) >= 0

# com mapa de valores: colunas com os nomes do mapa, na ordem dele, e percentual do primeiro
mapa = {'map': [{'Localizado': 'SIM'}, {'A confirmar': 'POSSIVELMENTE SIM'}, {'Não encontrado': 'NÃO'}]}
for lyr in (pontos, lotes):
    lyr.setEditorWidgetSetup(lyr.fields().indexOf('confirmado'), QgsEditorWidgetSetup('ValueMap', mapa))
dlg.gerar()
assert exportar.cabecalho_setor(dlg.res) == ['Setor', 'Localizado', 'A confirmar', 'Não encontrado', '(vazio)',
                                             'Total', '% Localizado'], exportar.cabecalho_setor(dlg.res)
assert exportar.linhas_setor(dlg.res) == [
    ['2', 3, 0, 1, 1, 5, 60.0],
    ['10', 1, 1, 0, 0, 2, 50.0],
    ['Fora dos setores', 0, 0, 1, 0, 1, 0.0],
    ['TOTAL', 4, 1, 2, 1, 8, 50.0]], exportar.linhas_setor(dlg.res)

arqs = exportar.exportar_csv(os.path.join(tempfile.mkdtemp(), 'r.csv'), dlg.res, dlg.info)
assert any(a.endswith('_setor.csv') for a in arqs) and any(a.endswith('_usuario_setor.csv') for a in arqs), arqs

# sem camada de setores: a aba some e o relatório fica como era
dlg.cb_setores.setCurrentIndex(0)
dlg.gerar()
assert 'setor' not in dlg.res and dlg.abas.indexOf(dlg.tb_setor) < 0 and dlg.abas.indexOf(dlg.tb_usuario_setor) < 0

print('OK')
