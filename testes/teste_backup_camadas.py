"""Teste do Backup de Camadas: campos e camadas que só mudam maiúsculas, "fid" repetido, estilo salvo.

Rodar na raiz do repositório (fica fora do zip do complemento):
    "C:\\Program Files\\QGIS 3.44.12\\bin\\python-qgis-ltr.bat" testes\\teste_backup_camadas.py
"""
import os, sqlite3, sys, tempfile, time
from unittest import mock
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt.QtGui import QColor
from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer

app = QgsApplication([], True)
app.initQgis()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'codigo'))
from ferramentas_solo.backup.plugin import BackupCamadas


def camada(nome, campos, linhas):
    lyr = QgsVectorLayer('Polygon?crs=EPSG:31983' + campos, nome, 'memory')
    for attrs in linhas:
        f = QgsFeature(lyr.fields()); f.setAttributes(attrs)
        f.setGeometry(QgsGeometry.fromWkt('Polygon((0 0,1 0,1 1,0 1,0 0))'))
        assert lyr.dataProvider().addFeature(f)
    return lyr


lotes = camada('Lotes', '&field=Area:double&field=area:double', [[1.5, 2.5]])
lotes2 = camada('lotes', '&field=k:string', [['outra camada']])       # mesmo nome sem maiúsculas
fid = camada('tab', '&field=fid:integer&field=v:string', [[1, 'a'], [1, 'b']])   # fid repetido
ok = camada('ok', '&field=v:string', [['x']])
ok.renderer().symbol().setColor(QColor('#ff0000'))
projeto = QgsProject.instance()
projeto.addMapLayers([lotes, lotes2, fid, ok])
pasta = tempfile.mkdtemp()
assert projeto.write(os.path.join(pasta, 'proj.qgz'))

iface = mock.MagicMock()
backup = BackupCamadas(iface)
backup.run()
limite = time.time() + 60
while backup.task is not None and time.time() < limite:
    app.processEvents(); time.sleep(0.05)
assert backup.task is None, 'o backup não terminou'

bar = iface.messageBar()
assert bar.pushSuccess.called, (bar.pushWarning.call_args, bar.pushCritical.call_args)
destino = bar.pushSuccess.call_args[0][1].split('Backup salvo em ')[1]
con = sqlite3.connect(destino)
tabelas = sorted(r[0] for r in con.execute("select table_name from gpkg_contents where table_name != 'layer_styles'"))
assert tabelas == ['Lotes', 'lotes_2', 'ok', 'tab'], tabelas
assert [r[1] for r in con.execute('pragma table_info("Lotes")')][-2:] == ['Area', 'area_2']
assert con.execute('select Area, area_2 from "Lotes"').fetchone() == (1.5, 2.5)
assert con.execute('select k from "lotes_2"').fetchone() == ('outra camada',)
assert sorted(con.execute('select fid_original, v from "tab"')) == [(1, 'a'), (1, 'b')]
assert sorted(r[0] for r in con.execute('select f_table_name from layer_styles')) == tabelas
estilo = QgsVectorLayer(destino + '|layername=ok', 'ok', 'ogr')
assert estilo.renderer().symbol().color().name() == '#ff0000', estilo.renderer().symbol().color().name()

# uma camada que falha não derruba o backup: as outras ficam e o aviso diz qual ficou de fora
from ferramentas_solo.backup import plugin as P
gravar = P.gravar
iface.reset_mock()
time.sleep(1.1)        # nome do arquivo tem segundos
with mock.patch.object(P, 'gravar', lambda c, d, t, cv, ctx: 'erro de teste' if t == 'ok' else gravar(c, d, t, cv, ctx)):
    backup.run()
    while backup.task is not None and time.time() < limite + 60:
        app.processEvents(); time.sleep(0.05)
aviso = iface.messageBar().pushWarning.call_args[0][1]
assert 'ficaram de fora' in aviso and aviso.endswith(': ok'), aviso
destino = aviso.split('Backup salvo em ')[1].split(', mas')[0]
con = sqlite3.connect(destino)
assert sorted(r[0] for r in con.execute("select table_name from gpkg_contents where table_name != 'layer_styles'")) \
    == ['Lotes', 'lotes_2', 'tab']

# coluna geometry de tabela aberta sem geometria (tumulo no PostGIS): grava como texto WKT, não falha
from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsField, QgsFields
campos = QgsFields(); campos.append(QgsField('id', QVariant.Int)); campos.append(QgsField('geom', QVariant.UserType, 'geometry'))
assert type(P.conversor(campos)) is P.RenomearETexto and type(P.conversor(lotes.fields())) is P.Renomear
conv = P.conversor(campos)
assert conv.fieldDefinition(campos.at(1)).type() == QVariant.String and conv.fieldDefinition(campos.at(0)).type() == QVariant.Int
assert conv.convert(1, QgsGeometry.fromWkt('Point (1 2)')) == 'Point (1 2)' and conv.convert(0, 7) == 7
print('OK')
