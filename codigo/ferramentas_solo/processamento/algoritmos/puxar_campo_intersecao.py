from qgis.core import (
    Qgis,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterString,
    QgsProcessingParameterEnum,
    QgsProcessingException,
    QgsField,
    QgsSpatialIndex,
    QgsCoordinateTransform,
    NULL
)

from ._util import sem_thread


class PuxarCampoIntersecao(QgsProcessingAlgorithm):

    DEST_LAYERS = 'DEST_LAYERS'
    SOURCE_LAYER = 'SOURCE_LAYER'
    SOURCE_FIELD = 'SOURCE_FIELD'
    TARGET_FIELD = 'TARGET_FIELD'
    MODE = 'MODE'

    def initAlgorithm(self, config=None):

        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.DEST_LAYERS,
                'Camadas que vão receber o campo',
                getattr(getattr(Qgis, 'ProcessingSourceType', None), 'VectorAnyGeometry',
                        QgsProcessing.TypeVectorAnyGeometry)
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.SOURCE_LAYER,
                'Camada maior / camada de origem',
                [QgsProcessing.TypeVectorAnyGeometry]
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.SOURCE_FIELD,
                'Campo da camada maior',
                parentLayerParameterName=self.SOURCE_LAYER
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.TARGET_FIELD,
                'Nome do campo destino'
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                'Modo de operação',
                options=[
                    'Criar novo campo',
                    'Atualizar campo existente'
                ],
                defaultValue=0
            )
        )

    def processAlgorithm(self, parameters, context, feedback):

        dest_layers = self.parameterAsLayerList(
            parameters,
            self.DEST_LAYERS,
            context
        )

        source_layer = self.parameterAsVectorLayer(
            parameters,
            self.SOURCE_LAYER,
            context
        )

        source_field = self.parameterAsString(
            parameters,
            self.SOURCE_FIELD,
            context
        )

        target_field = self.parameterAsString(
            parameters,
            self.TARGET_FIELD,
            context
        ).strip()

        mode = self.parameterAsEnum(
            parameters,
            self.MODE,
            context
        )

        if not dest_layers:
            raise QgsProcessingException('Selecione pelo menos uma camada destino.')

        if not source_layer:
            raise QgsProcessingException('Selecione a camada maior / camada de origem.')

        if not target_field:
            raise QgsProcessingException('Informe o nome do campo destino.')

        source_field_index = source_layer.fields().indexFromName(source_field)

        if source_field_index == -1:
            raise QgsProcessingException('Campo da camada maior não encontrado.')

        source_qfield = source_layer.fields()[source_field_index]
        source_crs = source_layer.crs()

        feedback.pushInfo('Criando índice espacial da camada maior...')

        spatial_index = QgsSpatialIndex()
        source_features = {}

        for source_feat in source_layer.getFeatures():

            if feedback.isCanceled():
                break

            if source_feat.hasGeometry() and not source_feat.geometry().isEmpty():
                spatial_index.addFeature(source_feat)
                source_features[source_feat.id()] = source_feat

        for layer in dest_layers:

            if feedback.isCanceled():
                break

            feedback.pushInfo(f'Processando: {layer.name()}')

            # Verifica e cria transformação de CRS caso as camadas tenham projeções diferentes
            dest_crs = layer.crs()
            transform = None
            if source_crs != dest_crs:
                transform = QgsCoordinateTransform(dest_crs, source_crs, context.transformContext())

            field_index = layer.fields().indexFromName(target_field)

            if mode == 0 and field_index == -1:

                new_field = QgsField(
                    target_field,
                    source_qfield.type(),
                    source_qfield.typeName(),
                    source_qfield.length(),
                    source_qfield.precision()
                )

                ok = layer.dataProvider().addAttributes([new_field])

                if not ok:
                    raise QgsProcessingException(
                        f'Não foi possível criar o campo "{target_field}" na camada {layer.name()}.'
                    )

                layer.updateFields()
                field_index = layer.fields().indexFromName(target_field)

            elif mode == 1 and field_index == -1:

                raise QgsProcessingException(
                    f'O campo "{target_field}" não existe na camada {layer.name()}.'
                )

            updates = {}
            total = layer.featureCount()

            for current, feat in enumerate(layer.getFeatures()):

                if feedback.isCanceled():
                    break

                if not feat.hasGeometry() or feat.geometry().isEmpty():
                    continue

                geom = feat.geometry()
                
                # Transforma a geometria do alvo para a mesma projeção da origem (se necessário)
                if transform:
                    geom.transform(transform)

                valor_novo = None
                maior_area = -1.0 

                candidate_ids = spatial_index.intersects(geom.boundingBox())

                for candidate_id in candidate_ids:

                    source_feat = source_features.get(candidate_id)

                    if not source_feat:
                        continue

                    source_geom = source_feat.geometry()

                    # Usa try/except para evitar crash caso exista algum erro topológico na feição (geometria inválida)
                    try:
                        if source_geom and source_geom.intersects(geom):
                            intersection_geom = source_geom.intersection(geom)
                            
                            # Apenas calcula a área se o resultado for um polígono válido
                            if not intersection_geom.isEmpty():
                                area_atual = intersection_geom.area()

                                if area_atual > maior_area:
                                    maior_area = area_atual
                                    valor_novo = source_feat[source_field]
                    except Exception as e:
                        # Ignora falhas de interseção causadas por geometria corrompida e pula para a próxima
                        continue

                if valor_novo not in [None, NULL, '']:

                    updates[feat.id()] = {
                        field_index: valor_novo
                    }

                if total:
                    feedback.setProgress(int(current * 100 / total))

            if updates:

                ok = layer.dataProvider().changeAttributeValues(updates)

                if not ok:
                    raise QgsProcessingException(
                        f'Erro ao atualizar atributos da camada {layer.name()}.'
                    )

                layer.triggerRepaint()

            feedback.pushInfo(
                f'Finalizado: {layer.name()} - {len(updates)} feições atualizadas.'
            )

        return {}

    def name(self):
        return 'puxar_campo_intersecao_maior_area'

    def displayName(self):
        return 'Puxar Campo por Interseção (Maior Área)'

    def group(self):
        return ''

    def groupId(self):
        return ''

    def createInstance(self):
        return PuxarCampoIntersecao()

    def flags(self):
        return sem_thread(super().flags())
