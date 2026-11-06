# -*- coding: utf-8 -*-
"""
Anexar / Atualizar Feições na Camada (Append)
---------------------------------------------
Baseado no complemento "Append Features to Layer" 2.2.0, de Germán Carrillo (GeoTux),
https://github.com/gacarrillor/AppendFeaturesToLayer — GPL v3.

Adaptado para o Ferramentas Solo (textos em português) com duas opções novas:
  • modo "atualizar só os atributos": a feição duplicada recebe os valores da origem e a
    geometria do destino fica como está;
  • "Não apagar com NULL": um campo NULL na origem não apaga o valor que já está preenchido
    no destino (vale também para a geometria: origem sem geometria não apaga a do destino).

Os nomes dos parâmetros são os mesmos do original (SOURCE_LAYER, TARGET_LAYER, ...), e os
modos 0 a 3 têm o mesmo número, para quem chama por script ou modelo.

This program is free software; you can redistribute it and/or modify it under the terms of
the GNU General Public License v3.0 as published by the Free Software Foundation.
"""
from qgis.core import (
    QgsEditError,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingOutputVectorLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsVectorDataProvider,
    QgsVectorLayerUtils,
    QgsWkbTypes,
    edit,
)

from ._util import sem_thread

try:
    from qgis.PyQt.QtCore import QVariant
except ImportError:  # pragma: no cover - Qt6 sem QVariant
    QVariant = None


def _nulo(valor):
    """NULL do QGIS (None no Qt6, QVariant nulo no Qt5) e datas/horas nulas."""
    if valor is None:
        return True
    if QVariant is not None and isinstance(valor, QVariant):
        return valor.isNull()
    eh_nulo = getattr(valor, "isNull", None)
    if callable(eh_nulo) and not isinstance(valor, QgsGeometry):
        try:
            return bool(eh_nulo())
        except TypeError:
            return False
    return False


def _sem_geometria():
    try:
        from qgis.core import Qgis
        return Qgis.GeometryType.Null
    except AttributeError:
        return QgsWkbTypes.NullGeometry


def _desconhecida():
    try:
        from qgis.core import Qgis
        return Qgis.GeometryType.Unknown
    except AttributeError:
        return QgsWkbTypes.UnknownGeometry


def _cap(nome):
    """Capacidade do provedor (Qgis.VectorProviderCapability no 3.40+, enum antigo antes)."""
    try:
        from qgis.core import Qgis
        return getattr(Qgis.VectorProviderCapability, nome)
    except AttributeError:
        return getattr(QgsVectorDataProvider, nome)


def _sem_geometria_req():
    try:
        from qgis.core import Qgis
        return Qgis.FeatureRequestFlag.NoGeometry
    except AttributeError:
        return QgsFeatureRequest.NoGeometry


def _converter(valor, tipo_destino, tipos_inteiros, tipos_decimais, tipos_texto):
    """Converte o valor da chave da origem para o tipo do campo do destino (para comparar)."""
    if QVariant is not None:
        try:
            q = QVariant(valor)
            if q.canConvert(tipo_destino) and q.convert(tipo_destino):
                return True, q.value()
        except TypeError:
            pass
    try:
        if tipo_destino in tipos_inteiros:
            num = float(str(valor).strip()) if isinstance(valor, str) else float(valor)
            return (True, int(num)) if num.is_integer() else (False, None)
        if tipo_destino in tipos_decimais:
            return True, float(str(valor).strip()) if isinstance(valor, str) else float(valor)
        if tipo_destino in tipos_texto:
            if isinstance(valor, float) and valor.is_integer():
                return True, str(int(valor))
            return True, str(valor)
    except (TypeError, ValueError):
        pass
    return False, None


def _tipos():
    """Grupos de tipos de campo (QMetaType no QGIS 3.38+, QVariant antes)."""
    inteiros, decimais, texto = set(), set(), set()
    for mod in ("QMetaType", "QVariant"):
        try:
            from qgis.PyQt import QtCore
            base = getattr(QtCore, mod)
        except (ImportError, AttributeError):
            continue
        enum = getattr(base, "Type", None)
        for nomes, grupo in ((("Int", "UInt", "LongLong", "ULongLong"), inteiros),
                             (("Double",), decimais),
                             (("QString", "String"), texto)):
            for n in nomes:
                for dono in (enum, base):
                    v = getattr(dono, n, None) if dono is not None else None
                    if v is not None:
                        grupo.add(v)
    return inteiros, decimais, texto


class AnexarFeicoesNaCamada(QgsProcessingAlgorithm):

    INPUT = 'SOURCE_LAYER'
    INPUT_FIELD = 'SOURCE_FIELD'
    OUTPUT = 'TARGET_LAYER'
    OUTPUT_FIELD = 'TARGET_FIELD'
    ACTION_ON_DUPLICATE = 'ACTION_ON_DUPLICATE'
    NAO_APAGAR_COM_NULO = 'NAO_APAGAR_COM_NULO'

    APPENDED_COUNT = 'APPENDED_COUNT'
    UPDATED_FEATURE_COUNT = 'UPDATED_FEATURE_COUNT'
    UPDATED_ONLY_GEOMETRY_COUNT = 'UPDATED_ONLY_GEOMETRY_COUNT'
    SKIPPED_COUNT = 'SKIPPED_COUNT'
    KEPT_VALUES_COUNT = 'KEPT_VALUES_COUNT'

    NO_ACTION = 0
    SKIP_FEATURE = 1
    UPDATE_EXISTING_FEATURE = 2
    UPDATE_EXISTING_GEOMETRY = 3
    UPDATE_EXISTING_ATTRIBUTES = 4

    ACOES = (
        "Só ANEXAR todas as feições, sem olhar duplicadas",
        "Se achar duplicada, PULAR a feição",
        "Se achar duplicada, ATUALIZAR a feição existente (atributos e geometria)",
        "Se achar duplicada, ATUALIZAR só a GEOMETRIA da feição existente",
        "Se achar duplicada, ATUALIZAR só os ATRIBUTOS da feição existente (mantém a geometria)",
    )

    def name(self):
        return 'appendfeaturestolayer'

    def displayName(self):
        return "Anexar / Atualizar Feições na Camada (Append)"

    def group(self):
        return ''

    def groupId(self):
        return ''

    def tags(self):
        return ("append,anexar,copiar,colar,inserir,atualizar,update,upsert,duplicadas,mesclar,"
                "etl,carregar,transferir,append features to layer").split(',')

    def shortHelpString(self):
        return (
            "Copia as feições de uma camada de origem para a camada de destino e, se quiser, "
            "atualiza as que já existem no destino.\n\n"
            "• Os campos com o mesmo nome nas duas camadas são copiados; campos que só existem na "
            "origem ficam de fora. A geometria é convertida para o tipo do destino quando precisa "
            "(simples → multi, polígono → linha etc.).\n"
            "• Escolha um campo da origem e um do destino para comparar (ex.: uuid, inscrição) e o "
            "que fazer quando o valor já existe no destino:\n"
            "   0) anexar tudo, sem olhar duplicadas;\n"
            "   1) pular a feição;\n"
            "   2) atualizar a feição existente — atributos e geometria;\n"
            "   3) atualizar só a geometria (atributos do destino ficam como estão);\n"
            "   4) atualizar só os atributos (a geometria do destino fica como está).\n"
            "• Não apagar com NULL: nos modos 2 e 4, um campo NULL na origem não apaga o valor "
            "que já está preenchido no destino — só os campos preenchidos na origem são "
            "atualizados. Nos modos 2 e 3, uma feição da origem sem geometria também não apaga a "
            "geometria do destino. Texto vazio ('') não é NULL: ele substitui normalmente.\n"
            "• Chave NULL nunca é considerada duplicada: a feição é anexada.\n"
            "• Use \"Somente feições selecionadas\" na camada de origem para levar só parte dela.\n\n"
            "Atenção: a camada de destino não pode estar em edição, e o resultado é gravado "
            "(salvo) direto nela ao final, como no complemento original.\n\n"
            "Baseado no complemento \"Append Features to Layer\", de Germán Carrillo (GeoTux), GPL v3."
        )

    def flags(self):
        return sem_thread(super().flags())

    def createInstance(self):
        return AnexarFeicoesNaCamada()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, "Camada de origem", [QgsProcessing.TypeVector]))
        self.addParameter(QgsProcessingParameterField(
            self.INPUT_FIELD, "Campo da origem para comparar", None, self.INPUT, optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.OUTPUT, "Camada de destino", [QgsProcessing.TypeVector]))
        self.addParameter(QgsProcessingParameterField(
            self.OUTPUT_FIELD, "Campo do destino para comparar", None, self.OUTPUT, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.ACTION_ON_DUPLICATE, "O que fazer com as feições duplicadas",
            options=list(self.ACOES), allowMultiple=False, defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.NAO_APAGAR_COM_NULO,
            "Não apagar com NULL (campo NULL na origem mantém o valor que já está no destino)",
            defaultValue=False))
        self.addOutput(QgsProcessingOutputVectorLayer(self.OUTPUT, "Camada de destino"))
        self.addOutput(QgsProcessingOutputNumber(self.APPENDED_COUNT, "Feições anexadas"))
        self.addOutput(QgsProcessingOutputNumber(self.UPDATED_FEATURE_COUNT, "Feições atualizadas"))
        self.addOutput(QgsProcessingOutputNumber(self.UPDATED_ONLY_GEOMETRY_COUNT, "Geometrias atualizadas"))
        self.addOutput(QgsProcessingOutputNumber(self.SKIPPED_COUNT, "Feições puladas"))
        self.addOutput(QgsProcessingOutputNumber(self.KEPT_VALUES_COUNT,
                                                 "Valores mantidos no destino (origem NULL)"))

    # ------------------------------------------------------------------ processamento
    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        campos_origem = self.parameterAsFields(parameters, self.INPUT_FIELD, context)
        target = self.parameterAsVectorLayer(parameters, self.OUTPUT, context)
        if target is None:
            raise QgsProcessingException("Escolha a camada de destino.")
        campos_destino = self.parameterAsFields(parameters, self.OUTPUT_FIELD, context)
        acao = self.parameterAsEnum(parameters, self.ACTION_ON_DUPLICATE, context)
        nao_apagar = self.parameterAsBoolean(parameters, self.NAO_APAGAR_COM_NULO, context)

        results = {self.OUTPUT: None,
                   self.APPENDED_COUNT: None,
                   self.UPDATED_FEATURE_COUNT: None,
                   self.UPDATED_ONLY_GEOMETRY_COUNT: None,
                   self.SKIPPED_COUNT: None,
                   self.KEPT_VALUES_COUNT: None}

        chave_origem = campos_origem[0] if campos_origem else ''
        chave_destino = campos_destino[0] if campos_destino else ''
        tipo_origem = tipo_destino = None

        if chave_origem:
            if source.fields().indexOf(chave_origem) == -1:
                feedback.reportError("\nAVISO: o campo '%s' não existe na camada de origem! Num modelo "
                                     "ETL, o campo '%s' precisa estar no mapeamento." % (chave_origem, chave_origem))
                return results
            tipo_origem = source.fields().field(chave_origem).type()

        if chave_destino:
            if target.fields().indexOf(chave_destino) == -1:
                feedback.reportError("\nAVISO: o campo '%s' não existe na camada de destino! Num modelo "
                                     "ETL, o campo '%s' precisa estar no mapeamento." % (chave_destino, chave_destino))
                return results
            tipo_destino = target.fields().field(chave_destino).type()

        if tipo_origem != tipo_destino:
            feedback.pushInfo("\nAVISO: os campos de comparação da origem e do destino são de tipos diferentes "
                              "(os valores são convertidos para comparar).")

        if chave_origem and chave_destino and acao == self.NO_ACTION:
            feedback.reportError("\nAVISO: como foram escolhidos campos para comparar, escolha também o que fazer "
                                 "com as feições duplicadas antes de rodar.")
            return results

        if acao != self.NO_ACTION and not (chave_origem and chave_destino):
            feedback.reportError("\nAVISO: para tratar as feições duplicadas, escolha o campo da origem e o campo "
                                 "do destino para comparar.")
            return results

        caps = target.dataProvider().capabilities()
        if not (caps & _cap("AddFeatures")):
            feedback.reportError("\nAVISO: a camada de destino não aceita incluir feições! Escolha outra camada.")
            return results

        origem_espacial = QgsWkbTypes.geometryType(source.wkbType()) != _sem_geometria()

        if acao in (self.UPDATE_EXISTING_FEATURE, self.UPDATE_EXISTING_ATTRIBUTES) \
                and not (caps & _cap("ChangeAttributeValues")):
            feedback.reportError("\nAVISO: a camada de destino não aceita alterar atributos! Escolha outra ação "
                                 "ou outra camada.")
            return results

        if acao == self.UPDATE_EXISTING_FEATURE and target.isSpatial() and not (caps & _cap("ChangeGeometries")):
            feedback.reportError("\nAVISO: a camada de destino não aceita alterar geometrias! Escolha \"atualizar "
                                 "só os atributos\" ou outra camada.")
            return results

        if acao == self.UPDATE_EXISTING_GEOMETRY:
            if not origem_espacial:
                feedback.reportError("\nAVISO: a camada de origem não tem geometria! Escolha outra ação ou outra "
                                     "camada de origem.")
                return results
            if not target.isSpatial():
                feedback.reportError("\nAVISO: a camada de destino não tem geometria! Escolha outra ação ou outra "
                                     "camada de destino.")
                return results
            if not (caps & _cap("ChangeGeometries")):
                feedback.reportError("\nAVISO: a camada de destino não aceita alterar geometrias! Escolha outra "
                                     "ação ou outra camada.")
                return results

        if target.isEditable():
            feedback.reportError("\nAVISO: feche a edição da camada '%s' (salve ou descarte) antes de rodar."
                                 % target.name())
            return results

        # correspondência de campos destino -> origem (pelo nome)
        mapping = dict()
        prov = target.dataProvider()
        for target_idx in target.fields().allAttributesList():
            target_field = target.fields().field(target_idx)
            if target_idx in target.primaryKeyAttributes():
                # PK uuid do PostGIS entra no mapeamento (as novas mantêm o uuid de origem)
                if prov.name() != 'postgres' or target_field.typeName() != "uuid":
                    # PK automática não dá para gravar
                    if prov.defaultValueClause(target_idx) not in ['', 'nextval(NULL)']:
                        continue
            if prov.storageType() == 'GPKG' and target_field.name() == 'fid':
                continue  # o fid do GeoPackage não se altera
            source_idx = source.fields().indexOf(target_field.name())
            if source_idx != -1:
                mapping[target_idx] = source_idx

        # valores da chave no destino {valor: [id1, id2], ...} (chave NULL fica de fora)
        valores_destino = dict()
        if chave_destino:
            req = QgsFeatureRequest()
            req.setFlags(_sem_geometria_req())
            req.setSubsetOfAttributes([chave_destino], target.fields())
            for f in target.getFeatures(req):
                v = f[chave_destino]
                if _nulo(v):
                    continue
                valores_destino.setdefault(v, []).append(int(f.id()))

        tipos = _tipos()
        total = 100.0 / source.featureCount() if source.featureCount() else 0
        dest_tipo = target.geometryType()
        dest_multi = QgsWkbTypes.isMultiType(target.wkbType())
        atualizar_attrs = acao in (self.UPDATE_EXISTING_FEATURE, self.UPDATE_EXISTING_ATTRIBUTES)
        atualizar_geom = (acao in (self.UPDATE_EXISTING_FEATURE, self.UPDATE_EXISTING_GEOMETRY)
                          and origem_espacial and target.isSpatial())
        novas = list()
        attrs_por_feicao = dict()
        geom_por_feicao = dict()
        n_puladas = 0
        n_sem_conversao = 0
        n_mantidos = 0
        n_geom_mantidas = 0
        duplicadas = set()

        for atual, in_feature in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(atual * total))

            ids_duplicados = None
            if chave_origem:
                achou, valor = self._achar_duplicada(in_feature[chave_origem], tipo_origem,
                                                     valores_destino, tipo_destino, tipos)
                if achou:
                    if acao == self.SKIP_FEATURE:
                        n_puladas += len(valores_destino[valor])
                        continue
                    ids_duplicados = valores_destino[valor]

            atualizar = ids_duplicados is not None and acao in (
                self.UPDATE_EXISTING_FEATURE, self.UPDATE_EXISTING_GEOMETRY, self.UPDATE_EXISTING_ATTRIBUTES)

            geom = QgsGeometry()
            if (not atualizar or atualizar_geom) and origem_espacial and in_feature.hasGeometry() \
                    and target.isSpatial():
                # converte a geometria para o tipo do destino (como o colar do QGIS)
                geom = in_feature.geometry()
                if not geom.isNull():
                    if dest_tipo != _desconhecida():
                        convertida = geom.convertToType(dest_tipo, dest_multi)
                        if convertida.isNull():
                            n_sem_conversao += 1
                            continue
                        geom = convertida
                    geom.avoidIntersections(QgsProject.instance().avoidIntersectionsLayers())

            attrs = {t: in_feature[s] for t, s in mapping.items()}

            if not atualizar:
                novas.append(QgsVectorLayerUtils.createFeature(target, geom, attrs))
                continue

            # nunca altera a chave primária de uma feição que já existe
            for pk in target.primaryKeyAttributes():
                attrs.pop(pk, None)

            req = QgsFeatureRequest().setFilterFids(ids_duplicados)
            if not atualizar_geom:
                req.setFlags(_sem_geometria_req())
            for t_f in target.getFeatures(req):
                fid = t_f.id()
                duplicadas.add(fid)
                if atualizar_attrs:
                    novos = attrs
                    if nao_apagar:
                        novos = {}
                        for idx, v in attrs.items():
                            if _nulo(v):
                                if not _nulo(t_f.attribute(idx)):
                                    n_mantidos += 1
                            else:
                                novos[idx] = v
                    attrs_por_feicao.setdefault(fid, {}).update(novos)
                if atualizar_geom:
                    if nao_apagar and geom.isNull():
                        if t_f.hasGeometry():
                            n_geom_mantidas += 1
                    else:
                        geom_por_feicao[fid] = geom

        # grava tudo de uma vez
        n_attrs = 0
        n_geom = 0
        n_sem_mudanca = 0
        res_add = False
        try:
            with edit(target):
                target.beginEditCommand("Anexando/atualizando feições...")
                for fid, valores in attrs_por_feicao.items():
                    if not valores:
                        n_sem_mudanca += 1
                        continue
                    if target.changeAttributeValues(fid, valores):
                        n_attrs += 1
                    else:
                        feedback.reportError("\nERRO: a feição de destino (id=%s) não pôde receber os "
                                             "atributos: %s." % (fid, valores))
                for fid, g in geom_por_feicao.items():
                    if target.changeGeometry(fid, g):
                        n_geom += 1
                    else:
                        feedback.reportError("\nERRO: a geometria da feição de destino (id=%s) não pôde "
                                             "ser atualizada." % fid)
                if novas:
                    res_add = target.addFeatures(novas)
                target.endEditCommand()
        except QgsEditError as e:
            if target.isEditable():
                target.rollBack()
            feedback.reportError("\nERRO: nenhuma feição foi anexada/atualizada em '%s', por causa do erro:\n%s\n"
                                 % (target.name(), repr(e)))
            return results

        nome = target.name() or target.source()
        if n_sem_conversao:
            feedback.reportError("\nAVISO: %d feição(ões) da origem ficaram de fora porque a geometria não pôde "
                                 "ser convertida para o tipo do destino." % n_sem_conversao)

        if acao == self.SKIP_FEATURE:
            feedback.pushInfo("\nPULADAS: %d feição(ões) duplicada(s) foram puladas ao copiar para '%s'."
                              % (n_puladas, nome))
            results[self.SKIPPED_COUNT] = n_puladas

        if atualizar_attrs:
            feedback.pushInfo("\nATUALIZADAS: %d de %d feição(ões) duplicada(s) tiveram os atributos "
                              "atualizados em '%s'%s." % (
                                  n_attrs, len(duplicadas), nome,
                                  " (a geometria ficou como estava)" if acao == self.UPDATE_EXISTING_ATTRIBUTES else ""))
            results[self.UPDATED_FEATURE_COUNT] = n_attrs

        if acao == self.UPDATE_EXISTING_FEATURE:
            feedback.pushInfo("\nGEOMETRIAS: %d geometria(s) atualizada(s)." % n_geom)
            results[self.UPDATED_ONLY_GEOMETRY_COUNT] = n_geom

        if acao == self.UPDATE_EXISTING_GEOMETRY:
            feedback.pushInfo("\nGEOMETRIAS ATUALIZADAS: %d de %d feição(ões) duplicada(s) tiveram a geometria "
                              "atualizada em '%s'." % (n_geom, len(duplicadas), nome))
            results[self.UPDATED_ONLY_GEOMETRY_COUNT] = n_geom

        if nao_apagar:
            if atualizar_attrs:
                feedback.pushInfo("\nNÃO APAGAR COM NULL: %d valor(es) preenchido(s) no destino foram mantidos "
                                  "porque a origem estava NULL%s." % (
                                      n_mantidos,
                                      "; %d feição(ões) não tinham nada preenchido para atualizar" % n_sem_mudanca
                                      if n_sem_mudanca else ""))
            if atualizar_geom and n_geom_mantidas:
                feedback.pushInfo("NÃO APAGAR COM NULL: %d geometria(s) do destino mantida(s) porque a feição "
                                  "da origem não tinha geometria." % n_geom_mantidas)
            results[self.KEPT_VALUES_COUNT] = n_mantidos

        results[self.APPENDED_COUNT] = 0
        if not novas:
            feedback.pushInfo("\nNENHUMA FEIÇÃO ANEXADA: não havia feições novas para anexar em '%s'." % nome)
        elif res_add:
            feedback.pushInfo("\nANEXADAS: %d de %d feição(ões) da origem foram anexadas em '%s'."
                              % (len(novas), source.featureCount(), nome))
            results[self.APPENDED_COUNT] = len(novas)
        else:
            feedback.reportError("\nERRO: as %d feição(ões) novas não puderam ser anexadas em '%s'. Às vezes é por "
                                 "causa de campos obrigatórios (NOT NULL) sem valor." % (len(novas), nome))

        results[self.OUTPUT] = target
        return results

    @staticmethod
    def _achar_duplicada(valor, tipo_origem, valores_destino, tipo_destino, tipos):
        """O valor da origem existe no destino? Primeiro como está; se os tipos forem
        diferentes, convertido para o tipo do destino. Chave NULL nunca é duplicada."""
        if _nulo(valor):
            return False, None
        if tipo_origem == tipo_destino or valor in valores_destino:
            return (True, valor) if valor in valores_destino else (False, None)
        ok, convertido = _converter(valor, tipo_destino, *tipos)
        if ok and not _nulo(convertido) and convertido in valores_destino:
            return True, convertido
        return False, None
