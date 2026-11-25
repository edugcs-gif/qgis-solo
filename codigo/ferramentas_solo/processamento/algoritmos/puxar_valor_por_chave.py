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
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest
)

from ._util import sem_geometria, sem_thread

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

    @staticmethod
    def _igual(novo, antigo):
        nulo_novo = novo is None or QgsVariantUtils.isNull(novo)
        nulo_antigo = antigo is None or QgsVariantUtils.isNull(antigo)
        if nulo_novo or nulo_antigo:
            return nulo_novo and nulo_antigo
        return type(novo) == type(antigo) and novo == antigo

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

        # 2. DESTINO: NÚMERO INTEIRO — pelo tipo, não pela precisão: float8 e numeric
        # sem escala do PostgreSQL (e REAL do GeoPackage) também informam precisão 0
        elif tipo_destino in (QVariant.Int, QVariant.UInt, QVariant.LongLong, QVariant.ULongLong):
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

        # só a chave e o valor, sem geometria: ler a camada inteira era o que mais demorava
        idx_destino = camada_destino.fields().indexOf(valor_destino_nome) if valor_destino_nome else -1
        campo_destino_obj = camada_destino.fields().at(idx_destino) if idx_destino != -1 else None
        # Campos com valor padrão "aplicar ao atualizar" (usuário, data da edição...). O QGIS recalcula
        # cada um relendo a feição inteira do arquivo a cada alteração, o que deixava a gravação muito
        # lenta; aqui eles são calculados nesta mesma leitura e gravados junto com o valor puxado.
        campos_auto = [i for i in range(camada_destino.fields().count())
                       if camada_destino.defaultValueDefinition(i).applyOnUpdate()
                       and camada_destino.defaultValueDefinition(i).expression()]
        if not campos_auto or apenas_selecao or idx_destino == -1:
            campos_auto = []
            requisicao.setFlags(sem_geometria())
            requisicao.setSubsetOfAttributes([i for i in (camada_destino.fields().indexOf(chave_destino_nome),
                                                          idx_destino) if i != -1])
        elif not any(QgsExpression(camada_destino.defaultValueDefinition(i).expression()).needsGeometry()
                     for i in campos_auto):
            requisicao.setFlags(sem_geometria())
        ctx_auto = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(camada_destino))

        # --- STEP 1: Mapear os dados da origem ---
        mapa_origem = {}
        campos_origem = fonte_origem.fields()
        i_chave_origem = campos_origem.indexOf(chave_origem_nome)
        i_valor_origem = campos_origem.indexOf(valor_origem_nome) if valor_origem_nome else -1
        req_origem = QgsFeatureRequest().setFlags(sem_geometria())
        req_origem.setSubsetOfAttributes([i for i in (i_chave_origem, i_valor_origem) if i != -1])
        for feat in fonte_origem.getFeatures(req_origem):
            chave_limpa = self._limpar_chave(feat.attribute(i_chave_origem))
            if chave_limpa is not None:
                val = feat.attribute(i_valor_origem) if i_valor_origem != -1 else True
                mapa_origem[chave_limpa] = val

        # --- STEP 2: Preparar índices e alterações ---
        ids_para_selecionar = []
        alteracoes_atributos = {}
        ja_iguais = 0
        i_chave_destino = camada_destino.fields().indexOf(chave_destino_nome)

        for feat in camada_destino.getFeatures(requisicao):
            chave_limpa_dest = self._limpar_chave(feat.attribute(i_chave_destino))
            
            if chave_limpa_dest and chave_limpa_dest in mapa_origem:
                ids_para_selecionar.append(feat.id())
                
                if not apenas_selecao and idx_destino != -1:
                    valor_bruto = mapa_origem[chave_limpa_dest]
                    novo_valor = self._converter_valor(valor_bruto, campo_destino_obj)
                    antigo = feat.attribute(idx_destino)
                    if self._igual(novo_valor, antigo):
                        ja_iguais += 1   # já está com esse valor: não regrava (nem dispara os valores automáticos)
                        continue
                    novos, antigos = {idx_destino: novo_valor}, {idx_destino: antigo}
                    if campos_auto:  # como o QGIS faz: cada campo automático já vê o valor novo
                        feat.setAttribute(idx_destino, novo_valor)
                        for i in campos_auto:
                            v = camada_destino.defaultValue(i, feat, ctx_auto)
                            antigos.setdefault(i, feat.attribute(i))
                            novos[i] = v
                            feat.setAttribute(i, v)
                    alteracoes_atributos[feat.id()] = (novos, antigos)

        # --- STEP 3: Aplicar Alterações Seguras ---
        if apenas_selecao:
            camada_destino.selectByIds(ids_para_selecionar)
        else:
            if idx_destino != -1 and alteracoes_atributos:
                # Nunca salva sozinho: fica tudo pendente na camada, e um único Ctrl+Z desfaz
                if not camada_destino.isEditable() and not camada_destino.startEditing():
                    feedback.reportError(
                        f"Não foi possível colocar a camada '{camada_destino.name()}' em edição.",
                        fatalError=True
                    )
                    return {}

                camada_destino.beginEditCommand("Puxar valor por chave comum (Ferramentas Solo)")
                for feat_id, (attrs, antigos) in alteracoes_atributos.items():
                    camada_destino.changeAttributeValues(feat_id, attrs, antigos, skipDefaultValues=True)
                camada_destino.endEditCommand()

                feedback.pushWarning(
                    "As alterações NÃO foram salvas: a camada ficou em edição para conferência. "
                    "Salve as edições, ou desfaça tudo com um Ctrl+Z."
                )
                camada_destino.triggerRepaint()

        feedback.pushInfo(f"Correspondências encontradas: {len(ids_para_selecionar)}")
        if not apenas_selecao:
            feedback.pushInfo(f"Atributos atualizados na camada '{camada_destino.name()}': {len(alteracoes_atributos)}")
            if ja_iguais:
                feedback.pushInfo(f"Já estavam com o mesmo valor (não regravados): {ja_iguais}")

        return {'REGISTROS_PROCESSADOS': len(ids_para_selecionar)}
