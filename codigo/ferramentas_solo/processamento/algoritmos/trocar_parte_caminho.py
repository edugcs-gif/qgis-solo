from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsFeatureSink,
    QgsProcessingException
)

from ._util import sem_thread


class TrocarParteCaminho(QgsProcessingAlgorithm):

    INPUT = 'INPUT'
    CAMADA_EDITAR = 'CAMADA_EDITAR'
    CAMPO = 'CAMPO'
    TEXTO_ANTIGO = 'TEXTO_ANTIGO'
    TEXTO_NOVO = 'TEXTO_NOVO'
    ATUALIZAR_ORIGINAL = 'ATUALIZAR_ORIGINAL'
    OUTPUT = 'OUTPUT'

    def tr(self, string):
        return QCoreApplication.translate('TrocarParteCaminho', string)

    def createInstance(self):
        return TrocarParteCaminho()

    def flags(self):
        return sem_thread(super().flags())

    def name(self):
        return 'trocar_parte_caminho'

    def displayName(self):
        return 'Trocar parte de caminho em campo'

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return 'Troca uma parte de um caminho dentro de um campo. Pode criar nova camada ou atualizar a camada original.'

    def initAlgorithm(self, config=None):

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                'Camada',
                [QgsProcessing.TypeVector]
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO,
                'Campo com o caminho',
                parentLayerParameterName=self.INPUT,
                type=QgsProcessingParameterField.String
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.TEXTO_ANTIGO,
                'Parte antiga do caminho',
                defaultValue=r'\\192.168.1.150\D\Imagens_360'
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.TEXTO_NOVO,
                'Parte nova do caminho',
                defaultValue='\\\\solonas\\Imagens_360'
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ATUALIZAR_ORIGINAL,
                'Atualizar a camada original em vez de criar nova camada',
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                'Camada corrigida',
                optional=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):

        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        campo = self.parameterAsString(parameters, self.CAMPO, context)
        texto_antigo = self.parameterAsString(parameters, self.TEXTO_ANTIGO, context)
        texto_novo = self.parameterAsString(parameters, self.TEXTO_NOVO, context)
        atualizar_original = self.parameterAsBool(parameters, self.ATUALIZAR_ORIGINAL, context)

        if layer is None:
            raise QgsProcessingException('Camada inválida.')

        campo_index = layer.fields().indexFromName(campo)

        if campo_index == -1:
            raise QgsProcessingException('Campo não encontrado.')

        total = 100.0 / layer.featureCount() if layer.featureCount() else 0

        if atualizar_original:

            if not layer.isEditable():
                layer.startEditing()

            alteracoes = {}

            for current, feat in enumerate(layer.getFeatures()):

                if feedback.isCanceled():
                    break

                valor = feat[campo]

                if valor:
                    valor_corrigido = str(valor).replace(texto_antigo, texto_novo)

                    if valor_corrigido != valor:
                        alteracoes[feat.id()] = {campo_index: valor_corrigido}

                feedback.setProgress(int(current * total))

            if alteracoes:
                layer.dataProvider().changeAttributeValues(alteracoes)
                layer.updateFields()
                layer.triggerRepaint()

            feedback.pushInfo(f'{len(alteracoes)} feição(ões) atualizada(s) na camada original.')

            return {}

        else:

            fields = layer.fields()

            sink, dest_id = self.parameterAsSink(
                parameters,
                self.OUTPUT,
                context,
                fields,
                layer.wkbType(),
                layer.sourceCrs()
            )

            if sink is None:
                raise QgsProcessingException('Saída inválida.')

            for current, feat in enumerate(layer.getFeatures()):

                if feedback.isCanceled():
                    break

                valor = feat[campo]

                if valor:
                    valor_corrigido = str(valor).replace(texto_antigo, texto_novo)
                    feat[campo_index] = valor_corrigido

                sink.addFeature(feat, QgsFeatureSink.FastInsert)
                feedback.setProgress(int(current * total))

            return {self.OUTPUT: dest_id}
