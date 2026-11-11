"""Teste do 'Puxar Valor de Outra Camada/Tabela (Chave Comum)': conversão para o tipo do campo de destino.

Rodar na raiz do repositório (fica fora do zip do complemento):
    "C:\\Program Files\\QGIS 3.44.12\\bin\\python-qgis-ltr.bat" testes\\teste_puxar_valor_por_chave.py
"""
import os, sys
from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsApplication, QgsField

app = QgsApplication([], False)
app.initQgis()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'codigo'))
from ferramentas_solo.processamento.algoritmos.puxar_valor_por_chave import TransferirValoresTabelasECamadas

conv = TransferirValoresTabelasECamadas()._converter_valor

# float8 e numeric sem escala do PostgreSQL chegam como Double com comprimento -1 e precisão 0
float8 = QgsField('v', QVariant.Double, 'float8', -1, 0)
assert conv(123.45, float8) == 123.45, conv(123.45, float8)
assert conv(-2.7, float8) == -2.7, conv(-2.7, float8)
assert conv('1,5', float8) == 1.5, conv('1,5', float8)
assert conv(123.45, QgsField('v', QVariant.Double, 'numeric', 10, 2)) == 123.45

# campo inteiro continua recebendo inteiro
for tipo in (QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.ULongLong):
    r = conv(123.45, QgsField('n', tipo))
    assert r == 123 and isinstance(r, int), (tipo, r)

# ---- a ferramenta não salva sozinha: a camada fica em edição, com tudo pendente e um Ctrl+Z só
from qgis.core import NULL, QgsFeature, QgsVectorLayer
sys.path.append(os.path.join(QgsApplication.prefixPath(), 'python', 'plugins'))
from processing.core.Processing import Processing
Processing.initialize()
import processing
from ferramentas_solo.processamento.provider import ProvedorSolo

provedor = ProvedorSolo()  # guardar a referência, senão o Python descarta o provedor
QgsApplication.processingRegistry().addProvider(provedor)

destino = QgsVectorLayer('None?field=chave:string&field=v:double', 'destino', 'memory')
origem = QgsVectorLayer('None?field=chave:string&field=v:double', 'origem', 'memory')
for camada, valor in ((destino, None), (origem, 123.45)):
    for chave in ('a', 'b'):
        f = QgsFeature(camada.fields()); f.setAttributes([chave, valor])
        camada.dataProvider().addFeature(f)

processing.run('ferramentas_solo:transferir_valores_tabelas_camadas', {
    'CAMADA_DESTINO': destino, 'CAMPO_CHAVE_DESTINO': 'chave', 'CAMPO_RECEBER_VALOR': 'v',
    'CAMADA_ORIGEM': origem, 'CAMPO_CHAVE_ORIGEM': 'chave', 'CAMPO_FONTE_VALOR': 'v',
    'SOMENTE_SELECIONADAS_DESTINO': False, 'APENAS_SELECAO': False})

assert destino.isEditable() and destino.isModified(), 'a camada tinha que ficar em edição, com alterações pendentes'
assert [f['v'] for f in destino.getFeatures()] == [123.45, 123.45]
assert all(f['v'] == NULL for f in destino.dataProvider().getFeatures()), 'nada pode ter sido salvo'
assert destino.undoStack().count() == 1, destino.undoStack().count()

print('OK')
