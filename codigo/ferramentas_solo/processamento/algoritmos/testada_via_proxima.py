# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsWkbTypes,
    QgsSpatialIndex
)
import processing

class CalculadoraTestadasQGIS(QgsProcessingAlgorithm):

    INPUT_LOTES = 'INPUT_LOTES'
    INPUT_VIAS = 'INPUT_VIAS'
    TOLERANCIA = 'TOLERANCIA'
    BACKUP = 'BACKUP'
    OUTPUT = 'OUTPUT'
    OUTPUT_LINHAS = 'OUTPUT_LINHAS'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CalculadoraTestadasQGIS()

    def name(self):
        return 'calcular_testadas_via_proxima'

    def displayName(self):
        return self.tr('Calcular Testadas pela Via Mais Próxima')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("""
        Esta ferramenta calcula o comprimento das testadas principais de lotes urbanos
        utilizando o eixo das vias como referência.
        
        A testada principal é identificada como a aresta do lote cujo ponto médio
        está mais próximo do eixo das vias.
        """)

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
                self.tr('Camada de Vias (Eixos)'),
                [QgsProcessing.TypeVectorLine]
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.TOLERANCIA,
                self.tr('Tolerância (metros)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=10.0,
                minValue=0.1,
                maxValue=100.0
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.BACKUP,
                self.tr('Criar backup da camada original'),
                defaultValue=True
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
                self.tr('Linhas das Testadas (opcional)'),
                optional=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        lotes_layer = self.parameterAsVectorLayer(parameters, self.INPUT_LOTES, context)
        vias_layer = self.parameterAsVectorLayer(parameters, self.INPUT_VIAS, context)
        tolerancia = self.parameterAsDouble(parameters, self.TOLERANCIA, context)

        if lotes_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT_LOTES))
        if vias_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT_VIAS))

        feedback.setProgressText('Iniciando cálculo das testadas...')

        # --- CORREÇÃO DA REPROJEÇÃO ---
        if lotes_layer.crs() != vias_layer.crs():
            feedback.pushInfo(f'Reprojetando vias de {vias_layer.crs().authid()} para {lotes_layer.crs().authid()}')
            reprojectado = processing.run("native:reprojectlayer", {
                'INPUT': vias_layer,
                'TARGET_CRS': lotes_layer.crs(),
                'OUTPUT': 'memory:'  # CORREÇÃO 1: Especificar saída em memória
            }, context=context, feedback=feedback)

            vias_layer = reprojectado['OUTPUT']  # CORREÇÃO 2: Receber o objeto de camada diretamente

        original_fields = lotes_layer.fields()
        fields = QgsFields()

        for field in original_fields:
            if field.name().lower() != 'testada':
                fields.append(field)

        fields.append(QgsField('testada', QVariant.Double, 'double', 10, 2))

        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            lotes_layer.wkbType(),
            lotes_layer.crs()
        )

        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        linha_sink = None
        linha_dest_id = None
        
        # CORREÇÃO 3: Verificação robusta do parâmetro opcional de linhas de saída
        if self.OUTPUT_LINHAS in parameters and parameters[self.OUTPUT_LINHAS] is not None:
            linha_fields = QgsFields()
            linha_fields.append(QgsField('lote_fid', QVariant.Int))
            linha_fields.append(QgsField('comprimento', QVariant.Double, 'double', 10, 2))
            linha_fields.append(QgsField('distancia_via', QVariant.Double, 'double', 10, 2))

            (linha_sink, linha_dest_id) = self.parameterAsSink(
                parameters,
                self.OUTPUT_LINHAS,
                context,
                linha_fields,
                QgsWkbTypes.LineString,
                lotes_layer.crs()
            )

        feedback.setProgressText('Criando índice espacial das vias...')
        vias_index = QgsSpatialIndex()
        vias_features = {}

        for feature in vias_layer.getFeatures():
            vias_index.addFeature(feature)
            vias_features[feature.id()] = feature

        lotes_features = list(lotes_layer.getFeatures())
        total_lotes = len(lotes_features)

        feedback.setProgressText(f'Processando {total_lotes} lotes...')

        estatisticas = {
            'processados': 0,
            'com_testada': 0,
            'sem_testada': 0,
            'com_erro': 0
        }

        for idx, lote_feature in enumerate(lotes_features):
            if feedback.isCanceled():
                break

            progress = int((idx / total_lotes) * 100)
            feedback.setProgress(progress)

            lote_geom = lote_feature.geometry()
            comprimento_testada = 0
            testada_geom = None
            distancia_via = 0

            if not lote_geom.isEmpty() and lote_geom.isGeosValid():
                try:
                    resultado = self._calcular_testada_lote(lote_geom, vias_features, vias_index, tolerancia)
                    if resultado:
                        comprimento_testada, testada_geom, distancia_via = resultado
                except Exception as e:
                    feedback.reportError(f'Erro no lote FID {lote_feature.id()}: {str(e)}')
                    estatisticas['com_erro'] += 1

            estatisticas['processados'] += 1
            if comprimento_testada > 0:
                estatisticas['com_testada'] += 1
            else:
                estatisticas['sem_testada'] += 1

            out_feature = QgsFeature(fields)
            out_feature.setGeometry(lote_geom)

            for field in original_fields:
                if field.name().lower() != 'testada':
                    out_feature.setAttribute(field.name(), lote_feature.attribute(field.name()))

            out_feature.setAttribute('testada', comprimento_testada)

            sink.addFeature(out_feature, QgsFeatureSink.FastInsert)

            if linha_sink and testada_geom and comprimento_testada > 0:
                linha_feature = QgsFeature(linha_fields)
                linha_feature.setGeometry(testada_geom)
                linha_feature.setAttribute('lote_fid', lote_feature.id())
                linha_feature.setAttribute('comprimento', comprimento_testada)
                linha_feature.setAttribute('distancia_via', distancia_via)
                linha_sink.addFeature(linha_feature, QgsFeatureSink.FastInsert)

        feedback.setProgressText('Cálculo concluído!')
        feedback.pushInfo('=' * 50)
        feedback.pushInfo('RELATÓRIO DE PROCESSAMENTO')
        feedback.pushInfo('=' * 50)
        feedback.pushInfo(f'Total de lotes processados: {estatisticas["processados"]}')
        feedback.pushInfo(f'Lotes com testada identificada: {estatisticas["com_testada"]}')
        feedback.pushInfo(f'Lotes sem testada: {estatisticas["sem_testada"]}')
        feedback.pushInfo(f'Lotes com erro: {estatisticas["com_erro"]}')
        if estatisticas["processados"] > 0:
            taxa = (estatisticas["com_testada"] / estatisticas["processados"]) * 100
            feedback.pushInfo(f'Taxa de sucesso: {taxa:.1f}%')
        feedback.pushInfo('=' * 50)

        result = {self.OUTPUT: dest_id}
        if linha_dest_id:
            result[self.OUTPUT_LINHAS] = linha_dest_id

        return result

    def _calcular_testada_lote(self, lote_geom, vias_features, vias_index, tolerancia):
        try:
            lote_buffer = lote_geom.buffer(tolerancia, 8)
            candidate_ids = vias_index.intersects(lote_buffer.boundingBox())

            vias_proximas = []
            for via_id in candidate_ids:
                via_feature = vias_features[via_id]
                via_geom = via_feature.geometry()
                if lote_buffer.intersects(via_geom):
                    vias_proximas.append(via_geom)

            if not vias_proximas:
                return None

            bordas = self._extrair_bordas_lote(lote_geom)
            if not bordas:
                return None

            resultado = self._encontrar_testada_principal(bordas, vias_proximas, tolerancia)
            return resultado

        except Exception as e:
            raise Exception(f"Erro no cálculo da testada: {str(e)}")

    def _extrair_bordas_lote(self, lote_geom):
        if lote_geom.type() != QgsWkbTypes.PolygonGeometry:
            return []

        bordas = []

        try:
            if lote_geom.isMultipart():
                multipolygon = lote_geom.asMultiPolygon()
                if multipolygon and len(multipolygon) > 0:
                    polygon = multipolygon[0]
                else:
                    return []
            else:
                polygon = lote_geom.asPolygon()

            if not polygon or len(polygon) == 0:
                return []

            ring = polygon[0]
            if len(ring) < 4:
                return []

            for i in range(len(ring) - 1):
                p1 = ring[i]
                p2 = ring[i + 1]

                if p1 != p2:
                    linha = QgsGeometry.fromPolylineXY([p1, p2])
                    if not linha.isEmpty():
                        bordas.append(linha)

        except Exception:
            try:
                boundary = lote_geom.boundary()
                if boundary and boundary.type() == QgsWkbTypes.LineGeometry:
                    if boundary.isMultipart():
                        multiline = boundary.asMultiPolyline()
                        for line in multiline:
                            for i in range(len(line) - 1):
                                seg = QgsGeometry.fromPolylineXY([line[i], line[i+1]])
                                if not seg.isEmpty():
                                    bordas.append(seg)
                    else:
                        line = boundary.asPolyline()
                        for i in range(len(line) - 1):
                            seg = QgsGeometry.fromPolylineXY([line[i], line[i+1]])
                            if not seg.isEmpty():
                                bordas.append(seg)
            except:
                pass

        return bordas

    def _encontrar_testada_principal(self, bordas, vias_proximas, tolerancia):
        melhor_comprimento = 0
        melhor_borda = None
        menor_distancia = float('inf')

        for borda in bordas:
            try:
                comprimento_borda = borda.length()
                if comprimento_borda < 0.1:
                    continue

                ponto_medio = borda.interpolate(comprimento_borda / 2)

                distancia_minima = float('inf')
                for via_geom in vias_proximas:
                    try:
                        distancia = ponto_medio.distance(via_geom)
                        distancia_minima = min(distancia_minima, distancia)
                    except:
                        continue

                if (distancia_minima < menor_distancia and 
                    distancia_minima <= tolerancia and
                    comprimento_borda > melhor_comprimento * 0.5):

                    menor_distancia = distancia_minima
                    melhor_comprimento = comprimento_borda
                    melhor_borda = borda

            except Exception:
                continue

        if melhor_borda and menor_distancia <= tolerancia:
            return (melhor_comprimento, melhor_borda, menor_distancia)
        else:
            return None
