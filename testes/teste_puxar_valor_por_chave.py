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

print('OK')
