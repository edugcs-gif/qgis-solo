import re
import unicodedata
import warnings
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)
import processing

warnings.filterwarnings('ignore')


class CalculadoraTestadasQGIS(QgsProcessingAlgorithm):

    INPUT_LOTES = 'INPUT_LOTES'
    INPUT_VIAS = 'INPUT_VIAS'
    TOLERANCIA = 'TOLERANCIA'
    OUTPUT = 'OUTPUT'
    OUTPUT_LINHAS = 'OUTPUT_LINHAS'

    CAMPO_LOGRADOURO_LOTE = 'logradouro'
    CAMPO_NOME_VIA = 'Nome'
    CAMPO_TESTADA = 'testada'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CalculadoraTestadasQGIS()

    def name(self):
        return 'calcular_testadas_logradouro'

    def displayName(self):
        return self.tr('Calcular Testadas pelo Logradouro do Lote')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr(
            """
            Calcula a testada dos lotes utilizando o logradouro cadastrado como referência.

            1. Obtém o logradouro do lote.
            2. Filtra as vias da camada correspondente pelo nome.
            3. Identifica a borda do lote mais próxima da via.
            4. Calcula o comprimento dessa borda e grava no campo 'testada'.
            """
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LOTES,
                self.tr('Camada de Lotes'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_VIAS,
                self.tr('Camada de Vias com Nome'),
                [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TOLERANCIA,
                self.tr('Tolerância entre lote e via (metros)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=15.0,
                minValue=0.1,
                maxValue=100.0
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                self.tr('Lotes com Testadas')
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LINHAS,
                self.tr('Linhas das Testadas'),
                optional=True
            )
        )

    def _normalizar_nome(self, valor):
        if valor is None:
            return ''
        texto = str(valor).strip().upper()
        texto = unicodedata.normalize('NFD', texto)
        texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
        texto = re.sub(r'[^A-Z0-9\s]', ' ', texto)
        return re.sub(r'\s+', ' ', texto).strip()

    def _encontrar_campo(self, layer, nome_procurado):
        nome_normalizado = self._normalizar_nome(nome_procurado)
        for campo in layer.fields():
            if self._normalizar_nome(campo.name()) == nome_normalizado:
                return campo.name()
        return None

    def processAlgorithm(self, parameters, context, feedback):
        lotes_layer = self.parameterAsVectorLayer(parameters, self.INPUT_LOTES, context)
        vias_layer = self.parameterAsVectorLayer(parameters, self.INPUT_VIAS, context)
        tolerancia = self.parameterAsDouble(parameters, self.TOLERANCIA, context)

        if not lotes_layer or not vias_layer:
            raise QgsProcessingException('Camadas de entrada inválidas.')

        campo_logradouro = self._encontrar_campo(lotes_layer, self.CAMPO_LOGRADOURO_LOTE)
        campo_nome_via = self._encontrar_campo(vias_layer, self.CAMPO_NOME_VIA)

        if not campo_logradouro:
            raise QgsProcessingException(f'Campo "{self.CAMPO_LOGRADOURO_LOTE}" não encontrado na camada de lotes.')
        if not campo_nome_via:
            raise QgsProcessingException(f'Campo "{self.CAMPO_NOME_VIA}" não encontrado na camada de vias.')

        # Reprojeção de Vias se os CRS forem diferentes
        if lotes_layer.crs() != vias_layer.crs():
            feedback.pushInfo(f'Reprojetando vias para o CRS da camada de lotes ({lotes_layer.crs().authid()})...')
            reprojectado = processing.run(
                'native:reprojectlayer',
                {
                    'INPUT': vias_layer,
                    'TARGET_CRS': lotes_layer.crs(),
                    'OUTPUT': 'memory:'
                },
                context=context,
                feedback=feedback
            )
            vias_layer = reprojectado['OUTPUT']

        # Campos da camada de saída
        original_fields = lotes_layer.fields()
        fields = QgsFields()
        for field in original_fields:
            if field.name().lower() != self.CAMPO_TESTADA.lower():
                fields.append(field)
        fields.append(QgsField(self.CAMPO_TESTADA, QVariant.Double, 'double', 10, 2))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, lotes_layer.wkbType(), lotes_layer.crs()
        )

        linha_sink, linha_dest_id = None, None
        if self.OUTPUT_LINHAS in parameters and parameters[self.OUTPUT_LINHAS]:
            linha_fields = QgsFields()
            linha_fields.append(QgsField('lote_fid', QVariant.Int))
            linha_fields.append(QgsField('logradouro', QVariant.String))
            linha_fields.append(QgsField('comprimento', QVariant.Double, 'double', 10, 2))
            linha_fields.append(QgsField('distancia_via', QVariant.Double, 'double', 10, 2))

            linha_sink, linha_dest_id = self.parameterAsSink(
                parameters, self.OUTPUT_LINHAS, context, linha_fields,
                QgsWkbTypes.LineString, lotes_layer.crs()
            )

        # Indexação das Vias por Nome Normalizado
        vias_features = {}
        vias_por_nome = {}

        for via_feat in vias_layer.getFeatures():
            vias_features[via_feat.id()] = via_feat
            nome_norm = self._normalizar_nome(via_feat.attribute(campo_nome_via))
            if nome_norm:
                if nome_norm not in vias_por_nome:
                    vias_por_nome[nome_norm] = []
                vias_por_nome[nome_norm].append(via_feat.id())

        lotes_features = list(lotes_layer.getFeatures())
        total_lotes = len(lotes_features)

        estatisticas = {
            'processados': 0,
            'com_testada': 0,
            'sem_testada': 0,
            'sem_logradouro': 0,
            'logradouro_nao_encontrado': 0
        }

        for idx, lote_feature in enumerate(lotes_features):
            if feedback.isCanceled():
                break

            if total_lotes > 0:
                feedback.setProgress(int((idx / total_lotes) * 100))

            lote_geom = lote_feature.geometry()
            comprimento_testada = 0.0
            testada_geom = None
            distancia_via = 0.0

            valor_logradouro = lote_feature.attribute(campo_logradouro)
            logradouro_norm = self._normalizar_nome(valor_logradouro)

            if not logradouro_norm:
                estatisticas['sem_logradouro'] += 1
            elif logradouro_norm not in vias_por_nome:
                estatisticas['logradouro_nao_encontrado'] += 1
            elif not lote_geom.isEmpty() and lote_geom.isGeosValid():
                ids_vias = vias_por_nome[logradouro_norm]
                vias_proximas = []

                for via_id in ids_vias:
                    v_geom = vias_features[via_id].geometry()
                    if lote_geom.distance(v_geom) <= tolerancia:
                        vias_proximas.append(v_geom)

                if vias_proximas:
                    resultado = self._calcular_testada_lote(lote_geom, vias_proximas, tolerancia)
                    if resultado:
                        comprimento_testada, testada_geom, distancia_via = resultado

            estatisticas['processados'] += 1
            if comprimento_testada > 0:
                estatisticas['com_testada'] += 1
            else:
                estatisticas['sem_testada'] += 1

            # Grava feição do lote no Sink
            out_feature = QgsFeature(fields)
            out_feature.setGeometry(lote_geom)
            for field in original_fields:
                if field.name().lower() != self.CAMPO_TESTADA.lower():
                    out_feature.setAttribute(field.name(), lote_feature.attribute(field.name()))
            out_feature.setAttribute(self.CAMPO_TESTADA, comprimento_testada)
            sink.addFeature(out_feature, QgsFeatureSink.FastInsert)

            # Grava feição de linha no Sink (se habilitado)
            if linha_sink and testada_geom and comprimento_testada > 0:
                linha_feat = QgsFeature(linha_fields)
                linha_feat.setGeometry(testada_geom)
                linha_feat.setAttribute('lote_fid', lote_feature.id())
                linha_feat.setAttribute('logradouro', valor_logradouro)
                linha_feat.setAttribute('comprimento', comprimento_testada)
                linha_feat.setAttribute('distancia_via', distancia_via)
                linha_sink.addFeature(linha_feat, QgsFeatureSink.FastInsert)

        # Relatório Final
        feedback.pushInfo('=' * 50)
        feedback.pushInfo(f'Total de lotes processados: {estatisticas["processados"]}')
        feedback.pushInfo(f'Lotes com testada identificada: {estatisticas["com_testada"]}')
        feedback.pushInfo(f'Lotes sem logradouro preenchido: {estatisticas["sem_logradouro"]}')
        feedback.pushInfo(f'Logradouros não localizados nas vias: {estatisticas["logradouro_nao_encontrado"]}')
        feedback.pushInfo('=' * 50)

        result = {self.OUTPUT: dest_id}
        if linha_dest_id:
            result[self.OUTPUT_LINHAS] = linha_dest_id
        return result

    def _calcular_testada_lote(self, lote_geom, vias_proximas, tolerancia):
        bordas = self._extrair_bordas_lote(lote_geom)
        if not bordas:
            return None
        return self._encontrar_testada_principal(bordas, vias_proximas, tolerancia)

    def _extrair_bordas_lote(self, lote_geom):
        bordas = []

        # Trata polígonos simples e multi-polígonos
        if lote_geom.isMultipart():
            poligonos = lote_geom.asMultiPolygon()
        else:
            poligonos = [lote_geom.asPolygon()]

        for poly in poligonos:
            if not poly or len(poly) == 0:
                continue
            
            # Anel exterior do polígono
            anel_exterior = poly[0]
            if len(anel_exterior) < 4:
                continue

            # Quebra o perímetro do anel em segmentos simples de linha
            for i in range(len(anel_exterior) - 1):
                p1 = anel_exterior[i]
                p2 = anel_exterior[i + 1]
                if p1 != p2:
                    seg = QgsGeometry.fromPolylineXY([p1, p2])
                    if not seg.isEmpty():
                        bordas.append(seg)

        return bordas

    def _encontrar_testada_principal(self, bordas, vias_proximas, tolerancia):
        melhor_borda = None
        melhor_comprimento = 0.0
        menor_distancia = float('inf')

        for borda in bordas:
            comprimento_borda = borda.length()
            if comprimento_borda < 0.20:  # Descarta vértices mínimos / chanfros
                continue

            ponto_medio = borda.interpolate(comprimento_borda / 2.0)
            distancia_minima = float('inf')

            for via_geom in vias_proximas:
                dist = ponto_medio.distance(via_geom)
                if dist < distancia_minima:
                    distancia_minima = dist

            if distancia_minima <= tolerancia:
                if distancia_minima < menor_distancia:
                    menor_distancia = distancia_minima
                    melhor_comprimento = comprimento_borda
                    melhor_borda = borda

        if melhor_borda:
            return (round(melhor_comprimento, 2), melhor_borda, round(menor_distancia, 2))
        return None
