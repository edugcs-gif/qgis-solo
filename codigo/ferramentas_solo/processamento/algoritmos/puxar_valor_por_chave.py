import re
from datetime import datetime, date, time

from qgis.PyQt.QtCore import QVariant, QDate, QTime, QDateTime, QMetaType
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterBoolean,
    QgsVariantUtils,
    QgsFeatureRequest
)

from ._util import sem_thread

class TransferirValoresTabelasECamadas(QgsProcessingAlgorithm):
    CAMADA_DESTINO = 'CAMADA_DESTINO'
    CAMPO_CHAVE_DESTINO = 'CAMPO_CHAVE_DESTINO'
    CAMPO_RECEBER_VALOR = 'CAMPO_RECEBER_VALOR'
    
    CAMADA_ORIGEM = 'CAMADA_ORIGEM'
    CAMPO_CHAVE_ORIGEM = 'CAMPO_CHAVE_ORIGEM'
    CAMPO_FONTE_VALOR = 'CAMPO_FONTE_VALOR'
    
    APENAS_SELECAO = 'APENAS_SELECAO'
    SOMENTE_SELECIONADAS_DESTINO = 'SOMENTE_SELECIONADAS_DESTINO'

    def tr(self, string):
        return string

    def createInstance(self):
        return TransferirValoresTabelasECamadas()

    def flags(self):
        return sem_thread(super().flags())

    def name(self):
        return 'transferir_valores_tabelas_camadas'

    def displayName(self):
        return self.tr('Puxar Valor de Outra Camada/Tabela (Chave Comum)')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAMADA_DESTINO,
                self.tr('Camada/Tabela de Destino (Entrada)'),
                [QgsProcessing.TypeVector]
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_CHAVE_DESTINO,
                self.tr('Campo Chave (Destino)'),
                parentLayerParameterName=self.CAMADA_DESTINO
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_RECEBER_VALOR,
                self.tr('Campo que Receberá o Valor (Destino)'),
                parentLayerParameterName=self.CAMADA_DESTINO,
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.CAMADA_ORIGEM,
                self.tr('Camada/Tabela de Origem (Fonte)'),
                [QgsProcessing.TypeVector]
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_CHAVE_ORIGEM,
                self.tr('Campo Chave (Origem)'),
                parentLayerParameterName=self.CAMADA_ORIGEM
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.CAMPO_FONTE_VALOR,
                self.tr('Campo com o Valor a Copiar (Origem)'),
                parentLayerParameterName=self.CAMADA_ORIGEM,
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SOMENTE_SELECIONADAS_DESTINO,
                self.tr('Processar apenas feições selecionadas (Destino)'),
                defaultValue=True
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.APENAS_SELECAO,
                self.tr('Apenas Selecionar correspondências (Sem alterar dados)'),
                defaultValue=False
            )
        )

    def _limpar_chave(self, valor):
        if QgsVariantUtils.isNull(valor) or valor is None:
            return None
        texto = str(valor).strip().lower()
        if texto in ['', 'null', 'none']:
            return None
        return texto

    def _converter_valor(self, valor, campo_destino):
        """
        Converte dinamicamente o 'valor' de origem para o tipo nativo do 'campo_destino'.
        """
        if QgsVariantUtils.isNull(valor) or valor is None:
            return None

        tipo_destino = campo_destino.type()

        # 1. DESTINO: TEXTO / STRING
        if tipo_destino in (QVariant.String, QVariant.Char, getattr(QMetaType, 'QString', -1)):
            if isinstance(valor, (QDateTime, QDate, QTime)):
                return valor.toString("yyyy-MM-dd HH:mm:ss") if isinstance(valor, QDateTime) else valor.toString("yyyy-MM-dd")
            return str(valor)

        # 2. DESTINO: NÚMERO INTEIRO (Int, LongLong, UInt, etc.)
        elif campo_destino.isNumeric() and campo_destino.precision() == 0:
            try:
                if isinstance(valor, bool):
                    return 1 if valor else 0
                val_str = str(valor).replace(',', '.').strip()
                return int(float(val_str))
            except (ValueError, TypeError):
                return None

        # 3. DESTINO: NÚMERO DECIMAL (Double, Real)
        elif campo_destino.isNumeric():
            try:
                val_str = str(valor).replace(',', '.').strip()
                return float(val_str)
            except (ValueError, TypeError):
                return None

        # 4. DESTINO: BOOLEANO
        elif tipo_destino in (QVariant.Bool, getattr(QMetaType, 'Bool', -1)):
            if isinstance(valor, bool):
                return valor
            val_str = str(valor).strip().lower()
            if val_str in ('1', 'true', 't', 'sim', 's', 'v', 'verdadeiro'):
                return True
            if val_str in ('0', 'false', 'f', 'não', 'nao', 'n', 'falso'):
                return False
            return None

        # 5. DESTINO: DATA (QDate)
        elif tipo_destino in (QVariant.Date, getattr(QMetaType, 'QDate', -1)):
            if isinstance(valor, QDate):
                return valor
            if isinstance(valor, QDateTime):
                return valor.date()
            if isinstance(valor, (datetime, date)):
                return QDate(valor.year, valor.month, valor.day)
            
            dt = QDate.fromString(str(valor).strip()[:10], "yyyy-MM-dd")
            if not dt.isValid():
                dt = QDate.fromString(str(valor).strip()[:10], "dd/MM/yyyy")
            return dt if dt.isValid() else None

        # 6. DESTINO: DATA E HORA (QDateTime)
        elif tipo_destino in (QVariant.DateTime, getattr(QMetaType, 'QDateTime', -1)):
            if isinstance(valor, QDateTime):
                return valor
            if isinstance(valor, datetime):
                return QDateTime(valor)
            if isinstance(valor, QDate):
                return QDateTime(valor, QTime(0, 0, 0))
            
            dt = QDateTime.fromString(str(valor).strip(), "yyyy-MM-dd HH:mm:ss")
            if not dt.isValid():
                dt = QDateTime.fromString(str(valor).strip(), "dd/MM/yyyy HH:mm:ss")
            return dt if dt.isValid() else None

        # Fallback genérico para outros tipos
        return valor

    def processAlgorithm(self, parameters, context, feedback):
        camada_destino = self.parameterAsVectorLayer(parameters, self.CAMADA_DESTINO, context)
        fonte_origem = self.parameterAsSource(parameters, self.CAMADA_ORIGEM, context)

        chave_destino_nome = self.parameterAsString(parameters, self.CAMPO_CHAVE_DESTINO, context)
        chave_origem_nome = self.parameterAsString(parameters, self.CAMPO_CHAVE_ORIGEM, context)

        valor_destino_nome = self.parameterAsString(parameters, self.CAMPO_RECEBER_VALOR, context)
        valor_origem_nome = self.parameterAsString(parameters, self.CAMPO_FONTE_VALOR, context)

        apenas_selecao = self.parameterAsBool(parameters, self.APENAS_SELECAO, context)
        somente_selecionadas = self.parameterAsBool(parameters, self.SOMENTE_SELECIONADAS_DESTINO, context)

        if not camada_destino or not fonte_origem:
            feedback.reportError("Falha ao carregar as camadas de entrada.", fatalError=True)
            return {}

        # --- Define quais feições do destino serão processadas ---
        if somente_selecionadas:
            ids_selecionados = camada_destino.selectedFeatureIds()
            if not ids_selecionados:
                feedback.reportError(
                    "Nenhuma feição selecionada na camada de destino. "
                    "Selecione as feições desejadas ou desmarque a opção "
                    "'Processar apenas feições selecionadas'.",
                    fatalError=True
                )
                return {}
            requisicao = QgsFeatureRequest().setFilterFids(ids_selecionados)
            feedback.pushInfo(f"Feições selecionadas no destino: {len(ids_selecionados)}")
        else:
            requisicao = QgsFeatureRequest()

        # --- STEP 1: Mapear os dados da origem ---
        mapa_origem = {}
        for feat in fonte_origem.getFeatures():
            chave_limpa = self._limpar_chave(feat[chave_origem_nome])
            if chave_limpa is not None:
                val = feat[valor_origem_nome] if valor_origem_nome else True
                mapa_origem[chave_limpa] = val

        # --- STEP 2: Preparar índices e alterações ---
        ids_para_selecionar = []
        alteracoes_atributos = {}
        
        idx_destino = camada_destino.fields().indexOf(valor_destino_nome) if valor_destino_nome else -1
        campo_destino_obj = camada_destino.fields().at(idx_destino) if idx_destino != -1 else None

        for feat in camada_destino.getFeatures(requisicao):
            chave_limpa_dest = self._limpar_chave(feat[chave_destino_nome])
            
            if chave_limpa_dest and chave_limpa_dest in mapa_origem:
                ids_para_selecionar.append(feat.id())
                
                if not apenas_selecao and idx_destino != -1:
                    valor_bruto = mapa_origem[chave_limpa_dest]
                    novo_valor = self._converter_valor(valor_bruto, campo_destino_obj)
                    alteracoes_atributos[feat.id()] = {idx_destino: novo_valor}

        # --- STEP 3: Aplicar Alterações Seguras ---
        if apenas_selecao:
            camada_destino.selectByIds(ids_para_selecionar)
        else:
            if idx_destino != -1 and alteracoes_atributos:
                ja_estava_editando = camada_destino.isEditable()

                if ja_estava_editando or camada_destino.startEditing():
                    for feat_id, attrs in alteracoes_atributos.items():
                        camada_destino.changeAttributeValues(feat_id, attrs)

                    if ja_estava_editando:
                        # Não faz commit para não gravar outras edições pendentes do usuário
                        feedback.pushInfo(
                            "A camada já estava em edição: alterações aplicadas, "
                            "mas NÃO salvas. Salve as edições manualmente."
                        )
                    else:
                        if not camada_destino.commitChanges():
                            erros = "; ".join(camada_destino.commitErrors())
                            camada_destino.rollBack()
                            feedback.reportError(f"Falha ao salvar as alterações: {erros}", fatalError=True)
                            return {}
                else:
                    # Fallback: edita via DataProvider (arquivos de rede ou bloqueados)
                    provider = camada_destino.dataProvider()
                    provider.changeAttributeValues(alteracoes_atributos)
                
                camada_destino.triggerRepaint()

        feedback.pushInfo(f"Correspondências encontradas: {len(ids_para_selecionar)}")
        if not apenas_selecao:
            feedback.pushInfo(f"Atributos atualizados na camada '{camada_destino.name()}': {len(alteracoes_atributos)}")

        return {'REGISTROS_PROCESSADOS': len(ids_para_selecionar)}
