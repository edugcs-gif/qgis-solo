# -*- coding: utf-8 -*-
"""
Algoritmo de Processing do QGIS:
Seleciona unidades cujo uuid_edificacao aponta para uma edificação
que pertence a um lote diferente do uuid_lote da própria unidade.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingOutputNumber,
    QgsProcessingException
)

from ._util import sem_thread


class SelecionarUnidadesInconsistentes(QgsProcessingAlgorithm):

    CAMADA_EDIF = 'CAMADA_EDIF'
    CAMADA_UNID = 'CAMADA_UNID'
    CAMPO_EDIF_UUID = 'CAMPO_EDIF_UUID'
    CAMPO_EDIF_LOTE = 'CAMPO_EDIF_LOTE'
    CAMPO_UNID_EDIF = 'CAMPO_UNID_EDIF'
    CAMPO_UNID_LOTE = 'CAMPO_UNID_LOTE'

    SAIDA_TOTAL = 'SAIDA_TOTAL'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return SelecionarUnidadesInconsistentes()

    def flags(self):
        return sem_thread(super().flags())

    def name(self):
        return 'selecionar_unidades_inconsistentes'

    def displayName(self):
        return self.tr('Selecionar Unidades com Edificação de Lote Errado')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr(
            'Verifica, para cada Unidade, se a Edificação referenciada '
            '(uuid_edificacao) pertence ao mesmo lote informado na Unidade '
            '(uuid_lote). Seleciona na camada de Unidades todos os registros '
            'onde essa correspondência estiver incorreta ou a edificação não existir.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAMADA_EDIF,
                self.tr('Camada de Edificações'),
                types=[QgsProcessing.TypeVector]
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAMADA_UNID,
                self.tr('Camada/Tabela de Unidades (sem geometria)'),
                types=[QgsProcessing.TypeVector]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_EDIF_UUID,
                self.tr('Campo UUID da Edificação'),
                parentLayerParameterName=self.CAMADA_EDIF,
                defaultValue='uuid'
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_EDIF_LOTE,
                self.tr('Campo UUID do Lote (na camada Edificações)'),
                parentLayerParameterName=self.CAMADA_EDIF,
                defaultValue='uuid_lote'
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_UNID_EDIF,
                self.tr('Campo UUID da Edificação (na camada Unidades)'),
                parentLayerParameterName=self.CAMADA_UNID,
                defaultValue='uuid_edificacao'
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_UNID_LOTE,
                self.tr('Campo UUID do Lote (na camada Unidades)'),
                parentLayerParameterName=self.CAMADA_UNID,
                defaultValue='uuid_lote'
            )
        )
        self.addOutput(
            QgsProcessingOutputNumber(
                self.SAIDA_TOTAL,
                self.tr('Total de unidades inconsistentes')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        layer_edif = self.parameterAsVectorLayer(parameters, self.CAMADA_EDIF, context)
        layer_unid = self.parameterAsVectorLayer(parameters, self.CAMADA_UNID, context)

        campo_edif_uuid = self.parameterAsString(parameters, self.CAMPO_EDIF_UUID, context)
        campo_edif_lote = self.parameterAsString(parameters, self.CAMPO_EDIF_LOTE, context)
        campo_unid_edif = self.parameterAsString(parameters, self.CAMPO_UNID_EDIF, context)
        campo_unid_lote = self.parameterAsString(parameters, self.CAMPO_UNID_LOTE, context)

        if layer_edif is None or layer_unid is None:
            raise QgsProcessingException(self.tr('Camadas de entrada inválidas.'))

        def normalizar(valor):
            if valor is None:
                return None
            return str(valor).strip().lower()

        # Monta dicionário: uuid_edificacao (normalizado) -> uuid_lote (normalizado)
        feedback.pushInfo('Lendo edificações...')
        edif_para_lote = {}
        total_edif = layer_edif.featureCount()
        for i, feat in enumerate(layer_edif.getFeatures()):
            if feedback.isCanceled():
                break
            uuid_edif = normalizar(feat[campo_edif_uuid])
            uuid_lote = normalizar(feat[campo_edif_lote])
            edif_para_lote[uuid_edif] = uuid_lote
            if total_edif:
                feedback.setProgress(int((i + 1) / total_edif * 50))

        # Verifica as unidades
        feedback.pushInfo('Verificando unidades...')
        ids_incorretos = []
        total_unid = layer_unid.featureCount()
        for i, feat in enumerate(layer_unid.getFeatures()):
            if feedback.isCanceled():
                break
            uuid_lote_unid = normalizar(feat[campo_unid_lote])
            uuid_edif_unid = normalizar(feat[campo_unid_edif])

            uuid_lote_da_edif = edif_para_lote.get(uuid_edif_unid, None)

            if uuid_lote_da_edif is None or uuid_lote_da_edif != uuid_lote_unid:
                ids_incorretos.append(feat.id())

            if total_unid:
                feedback.setProgress(50 + int((i + 1) / total_unid * 50))

        layer_unid.removeSelection()
        layer_unid.selectByIds(ids_incorretos)

        feedback.pushInfo(
            f'Total de unidades inconsistentes selecionadas: {len(ids_incorretos)}'
        )

        return {self.SAIDA_TOTAL: len(ids_incorretos)}
