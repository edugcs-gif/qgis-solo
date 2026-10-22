from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProject,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterBoolean,
    QgsProcessing
)
from qgis.utils import iface

from ._util import sem_thread

class SelecaoDinamicCamadas(QgsProcessingAlgorithm):
    CAMADA_ORIGEM = 'CAMADA_ORIGEM'
    CAMPO_ORIGEM = 'CAMPO_ORIGEM'
    CAMADA_DESTINO = 'CAMADA_DESTINO'
    CAMPO_DESTINO = 'CAMPO_DESTINO'
    APENAS_CORRESPONDENTES = 'APENAS_CORRESPONDENTES'

    def tr(self, string):
        return string

    def createInstance(self):
        return SelecaoDinamicCamadas()

    def flags(self):
        return sem_thread(super().flags())

    def name(self):
        return 'selecao_dinamica_camadas'

    def displayName(self):
        return self.tr('Comparar Camadas e Selecionar')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def initAlgorithm(self, config=None):
        # 1. Camada onde a seleção vai acontecer (Aceita COM e SEM geometria)
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAMADA_ORIGEM,
                self.tr('Camada Onde Selecionar (Origem)'),
                [QgsProcessing.TypeVector]
            )
        )
        # 2. Campo chave da camada de origem
        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_ORIGEM,
                self.tr('Campo Chave da Camada de Origem'),
                parentLayerParameterName=self.CAMADA_ORIGEM
            )
        )
        # 3. Camada de referência (Aceita COM e SEM geometria)
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAMADA_DESTINO,
                self.tr('Camada de Referência (Destino)'),
                [QgsProcessing.TypeVector]
            )
        )
        # 4. Campo chave da camada de destino
        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_DESTINO,
                self.tr('Campo Chave da Camada de Destino'),
                parentLayerParameterName=self.CAMADA_DESTINO
            )
        )
        # 5. Checkbox para decidir a lógica
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.APENAS_CORRESPONDENTES,
                self.tr('Selecionar APENAS os correspondentes (Se desmarcado, seleciona os inexistentes)'),
                defaultValue=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        camada_origem = self.parameterAsVectorLayer(parameters, self.CAMADA_ORIGEM, context)
        campo_origem = parameters[self.CAMPO_ORIGEM]
        
        camada_destino = self.parameterAsVectorLayer(parameters, self.CAMADA_DESTINO, context)
        campo_destino = parameters[self.CAMPO_DESTINO]
        
        apenas_correspondentes = self.parameterAsBoolean(parameters, self.APENAS_CORRESPONDENTES, context)

        # 1. Mapeia todos os valores existentes na camada de destino
        valores_destino = set()
        for feicao in camada_destino.getFeatures():
            val = feicao[campo_destino]
            if val is not None:
                valores_destino.add(str(val).strip())

        # 2. Varre a camada de origem aplicando a lógica escolhida
        ids_para_selecionar = []
        for feicao in camada_origem.getFeatures():
            valor_origem = feicao[campo_origem]
            valor_busca = str(valor_origem).strip() if valor_origem is not None else ""
            
            if apenas_correspondentes:
                if valor_busca in valores_destino:
                    ids_para_selecionar.append(feicao.id())
            else:
                if valor_busca not in valores_destino:
                    ids_para_selecionar.append(feicao.id())

        # 3. Executa a seleção na interface principal
        if iface:
            camada_origem.selectByIds(ids_para_selecionar)

        tipo_busca = "correspondentes" if apenas_correspondentes else "inexistentes"
        feedback.pushInfo(f'Sucesso! {len(ids_para_selecionar)} feições/registros {tipo_busca} foram identificados e selecionados.')

        return {}
