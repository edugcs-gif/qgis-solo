# -*- coding: utf-8 -*-
"""
Corrigir Geometrias na Própria Camada
-------------------------------------
Corrige as geometrias inválidas (GEOS) dos polígonos direto na camada, sem criar camada
nova. As alterações ficam no buffer de edição (um único Ctrl+Z desfaz tudo), a menos que
"Salvar as edições ao final" esteja marcado.

As feições que não puderam ser corrigidas são listadas no registro com fid, id e uuid
(quando a camada tem esses campos) e o motivo; ficam selecionadas na camada e, se pedido,
vão para uma tabela.
"""
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterVectorLayer,
    QgsWkbTypes,
)

from ._util import sem_thread

METODOS = ("Estrutura (recomendado para polígonos)",
           "Linework (o mesmo do \"Corrigir geometrias\" do QGIS)")
AVISO_AREA = 0.01          # avisa quando a correção muda a área mais que 1 %
MAX_LINHAS_LOG = 500


def _tipo_poligono():
    try:
        return Qgis.GeometryType.Polygon
    except AttributeError:
        return QgsWkbTypes.PolygonGeometry


def _campo(nome, inteiro=False):
    """Campo inteiro longo ou texto, com QMetaType (QGIS >= 3.38 / Qt6) ou QVariant (antes)."""
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(nome, QMetaType.Type.LongLong if inteiro else QMetaType.Type.QString)
    except (ImportError, AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(nome, QVariant.LongLong if inteiro else QVariant.String)


def _metodo(indice):
    try:
        return (Qgis.MakeValidMethod.Structure, Qgis.MakeValidMethod.Linework)[indice]
    except AttributeError:
        return None


class CorrigirGeometriasNaCamada(QgsProcessingAlgorithm):

    INPUT = "INPUT"
    SELECIONADAS = "SELECIONADAS"
    METODO = "METODO"
    MAIOR_PARTE = "MAIOR_PARTE"
    SELECIONAR_FALHAS = "SELECIONAR_FALHAS"
    SALVAR = "SALVAR"
    LISTA = "LISTA"

    def name(self):
        return "corrigir_geometrias_na_camada"

    def displayName(self):
        return "Corrigir Geometrias na Própria Camada"

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return (
            "Corrige as geometrias inválidas dos polígonos direto na camada escolhida, sem criar "
            "camada nova. Só as feições inválidas são alteradas.\n\n"
            "• As alterações ficam pendentes na edição da camada: revise e salve, ou desfaça tudo "
            "com um Ctrl+Z. Marque \"Salvar as edições ao final\" para gravar direto (só quando a "
            "camada não tinha outras edições pendentes).\n"
            "• As feições que não puderam ser corrigidas aparecem no registro com fid, id e uuid "
            "(quando a camada tem esses campos) e o motivo, ficam selecionadas na camada e podem "
            "ir para uma tabela (saída opcional).\n"
            "• Método Estrutura: mantém só polígonos e costuma preservar melhor a forma. Linework: o "
            "método do \"Corrigir geometrias\" nativo. Se o escolhido falhar, o outro é tentado.\n"
            "• Em camada de polígono simples, uma correção que divide o polígono em várias partes é "
            "listada como não corrigida, a menos que se marque para manter só a maior parte.\n"
            "• Correções que mudam a área em mais de 1 % são avisadas no registro para conferência."
        )

    def flags(self):
        return sem_thread(super().flags())

    def createInstance(self):
        return CorrigirGeometriasNaCamada()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT, "Camada de polígonos", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SELECIONADAS, "Somente feições selecionadas", defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum(
            self.METODO, "Método de correção", options=list(METODOS), defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.MAIOR_PARTE,
            "Em camada de polígono simples, manter só a maior parte quando a correção dividir o polígono",
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SELECIONAR_FALHAS, "Selecionar na camada as feições que não foram corrigidas",
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SALVAR, "Salvar as edições ao final", defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.LISTA, "Tabela das feições não corrigidas (opcional)", QgsProcessing.TypeVector,
            optional=True, createByDefault=False))
        self.addOutput(QgsProcessingOutputNumber("CORRIGIDAS", "Feições corrigidas"))
        self.addOutput(QgsProcessingOutputNumber("NAO_CORRIGIDAS", "Feições não corrigidas"))
        self.addOutput(QgsProcessingOutputNumber("JA_VALIDAS", "Feições que já eram válidas"))

    # ------------------------------------------------------------------ geometria
    @staticmethod
    def _so_poligonos(geom):
        """Só as partes de polígono (a correção pode devolver coleção com linhas/pontos)."""
        if geom is None or geom.isEmpty():
            return None
        if (geom.type() == _tipo_poligono()
                and QgsWkbTypes.flatType(geom.wkbType()) != QgsWkbTypes.GeometryCollection):
            return geom
        partes = [p for p in geom.asGeometryCollection()
                  if p.type() == _tipo_poligono() and not p.isEmpty() and p.area() > 0]
        if not partes:
            return None
        return QgsGeometry.collectGeometry(partes) if len(partes) > 1 else partes[0]

    @staticmethod
    def _partes(geom):
        return geom.asGeometryCollection() if geom.isMultipart() else [geom]

    def _tentar(self, geom, indice):
        metodo = _metodo(indice)
        if metodo is None:                      # QGIS sem escolha de método
            return geom.makeValid()
        return geom.makeValid(metodo, False)

    def _corrigir(self, geom, indice, multi, maior_parte, tem_z, tem_m):
        """Devolve (geometria corrigida ou None, motivo, observação)."""
        motivo = "a correção não funcionou"
        for i in (indice, 1 - indice):
            try:
                r = self._tentar(QgsGeometry(geom), i)
            except Exception as exc:
                motivo = "erro na correção: %s" % exc
                continue
            r = self._so_poligonos(r)
            if r is None:
                motivo = "a correção não deixou nenhum polígono (geometria colapsada, sem área)"
                continue
            if not r.isGeosValid():
                motivo = "continua inválida depois da correção"
                continue
            obs = ""
            partes = self._partes(r)
            if not multi and len(partes) > 1:
                if not maior_parte:
                    areas = sorted((p.area() for p in partes), reverse=True)
                    motivo = ("a correção divide o polígono em %d partes (%s m²) e a camada só aceita "
                              "polígono simples; para ficar com a maior, marque \"manter só a maior "
                              "parte\", ou corrija à mão"
                              % (len(partes), " + ".join(("%.2f" % a).replace(".", ",") for a in areas[:5])
                                 + (" + ..." if len(areas) > 5 else "")))
                    continue
                maior = max(partes, key=lambda p: p.area())
                obs = "mantida só a maior de %d partes" % len(partes)
                r = QgsGeometry(maior)
            if multi:
                r.convertToMultiType()
            elif r.isMultipart():
                r.convertToSingleType()
            if tem_z and not QgsWkbTypes.hasZ(r.wkbType()):
                r.get().addZValue(0)
                obs = (obs + "; " if obs else "") + "Z perdido na correção (gravado 0)"
            elif not tem_z and QgsWkbTypes.hasZ(r.wkbType()):
                r.get().dropZValue()
            if tem_m and not QgsWkbTypes.hasM(r.wkbType()):
                r.get().addMValue(0)
            elif not tem_m and QgsWkbTypes.hasM(r.wkbType()):
                r.get().dropMValue()
            return r, None, obs
        return None, motivo, ""

    # ------------------------------------------------------------------ execução
    def processAlgorithm(self, parameters, context, feedback):
        camada = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if camada is None:
            raise QgsProcessingException("Camada inválida.")
        prj = context.project()
        no_projeto = prj is not None and prj.mapLayer(camada.id()) is camada
        so_sel = self.parameterAsBool(parameters, self.SELECIONADAS, context)
        indice = self.parameterAsEnum(parameters, self.METODO, context)
        maior_parte = self.parameterAsBool(parameters, self.MAIOR_PARTE, context)
        selecionar = self.parameterAsBool(parameters, self.SELECIONAR_FALHAS, context)
        salvar = self.parameterAsBool(parameters, self.SALVAR, context)
        if not no_projeto and not salvar:
            raise QgsProcessingException(
                "A camada \"%s\" não está aberta no projeto, então as correções pendentes se "
                "perderiam ao terminar. Adicione a camada ao projeto e escolha-a na lista, ou marque "
                "\"Salvar as edições ao final\"." % camada.name())

        req = QgsFeatureRequest()
        if so_sel:
            ids = camada.selectedFeatureIds()
            if not ids:
                raise QgsProcessingException(
                    "Nenhuma feição selecionada. Selecione as feições ou desmarque "
                    "\"Somente feições selecionadas\".")
            req.setFilterFids(ids)

        campos = camada.fields()
        i_fid = campos.lookupField("fid")
        i_uuid = campos.lookupField("uuid")
        i_id = campos.lookupField("id")

        def limpo(v):
            return "" if v is None or str(v) in ("", "NULL") else str(v)

        def ident(f):
            """(fid, id, uuid): fid do campo "fid" quando existe (GeoPackage), senão o id da feição."""
            fid = limpo(f.attribute(i_fid)) if i_fid >= 0 else ""
            uid = f.attribute(i_uuid) if i_uuid >= 0 else None
            idv = f.attribute(i_id) if i_id >= 0 else None
            return fid or str(f.id()), limpo(idv), limpo(uid)

        wkb = camada.wkbType()
        multi = QgsWkbTypes.isMultiType(wkb)
        tem_z, tem_m = QgsWkbTypes.hasZ(wkb), QgsWkbTypes.hasM(wkb)

        feicoes = list(camada.getFeatures(req))
        total = len(feicoes)
        feedback.pushInfo("Verificando %d feição(ões) de \"%s\"..." % (total, camada.name()))

        correcoes, falhas, avisos = [], [], []
        ja_validas = 0
        for n, f in enumerate(feicoes):
            if feedback.isCanceled():
                break
            if total:
                feedback.setProgress(80.0 * n / total)
            g = f.geometry()
            if g is None or g.isNull() or g.isEmpty():
                falhas.append((f, "sem geometria"))
                continue
            if g.isGeosValid():
                ja_validas += 1
                continue
            nova, motivo, obs = self._corrigir(g, indice, multi, maior_parte, tem_z, tem_m)
            if nova is None:
                falhas.append((f, motivo))
                continue
            a0, a1 = abs(g.area()), nova.area()
            if a0 > 0 and abs(a1 - a0) / a0 > AVISO_AREA:
                obs = (obs + "; " if obs else "") + "área %.2f → %.2f (%+.1f%%)" % (a0, a1, 100.0 * (a1 - a0) / a0)
            correcoes.append((f, nova, obs))

        # ---------------- grava no buffer de edição (um único Ctrl+Z desfaz)
        estava_editando = camada.isEditable()
        corrigidas = []
        if correcoes:
            if not estava_editando and not camada.startEditing():
                raise QgsProcessingException("Não foi possível colocar a camada \"%s\" em edição." % camada.name())
            camada.beginEditCommand("Corrigir geometrias (Ferramentas Solo)")
            for n, (f, nova, obs) in enumerate(correcoes):
                if camada.changeGeometry(f.id(), nova):
                    corrigidas.append(f.id())
                    if obs:
                        avisos.append((f, obs))
                else:
                    falhas.append((f, "a camada recusou a geometria corrigida"))
                feedback.setProgress(80.0 + 20.0 * n / len(correcoes))
            if corrigidas:
                camada.endEditCommand()
            else:
                camada.destroyEditCommand()

        if salvar and corrigidas:
            if estava_editando:
                feedback.pushWarning(
                    "A camada já estava em edição com outras alterações: as correções foram feitas, "
                    "mas NÃO foram salvas, para não gravar o resto junto. Salve as edições manualmente.")
            elif not camada.commitChanges():
                erros = "; ".join(camada.commitErrors())
                raise QgsProcessingException("Erro ao salvar as edições: %s" % erros)
        camada.triggerRepaint()

        # ---------------- relatório
        feedback.pushInfo("")
        feedback.pushInfo("Já válidas (não alteradas): %d" % ja_validas)
        feedback.pushInfo("Corrigidas: %d" % len(corrigidas))
        feedback.pushInfo("Não corrigidas: %d" % len(falhas))
        if avisos:
            feedback.pushInfo("")
            feedback.pushWarning("Corrigidas, mas confira (%d):" % len(avisos))
            for f, obs in avisos[:MAX_LINHAS_LOG]:
                fid, idv, uid = ident(f)
                feedback.pushInfo("  fid %s | id %s | uuid %s | %s" % (fid, idv or "—", uid or "—", obs))
        if falhas:
            feedback.pushInfo("")
            feedback.reportError("NÃO CORRIGIDAS (%d) — fid | id | uuid | motivo:" % len(falhas))
            for f, motivo in falhas[:MAX_LINHAS_LOG]:
                fid, idv, uid = ident(f)
                feedback.reportError("  fid %s | id %s | uuid %s | %s" % (fid, idv or "—", uid or "—", motivo))
            if len(falhas) > MAX_LINHAS_LOG:
                feedback.reportError("  ... e mais %d (veja a tabela de saída)" % (len(falhas) - MAX_LINHAS_LOG))
            fids = ", ".join(ident(f)[0] for f, _m in falhas[:MAX_LINHAS_LOG])
            feedback.pushInfo("Para filtrar ou selecionar de novo: %s IN (%s)"
                              % ('"fid"' if i_fid >= 0 else "$id", fids))
            if selecionar:
                camada.selectByIds([f.id() for f, _m in falhas])
                feedback.pushInfo("As não corrigidas ficaram selecionadas em \"%s\"." % camada.name())
        if corrigidas and not salvar:
            feedback.pushInfo("Edições pendentes em \"%s\": revise e salve, ou desfaça com Ctrl+Z." % camada.name())

        resultado = {"CORRIGIDAS": len(corrigidas), "NAO_CORRIGIDAS": len(falhas), "JA_VALIDAS": ja_validas}
        if parameters.get(self.LISTA) is not None:
            tab = QgsFields()
            tab.append(_campo("fid", inteiro=True))
            tab.append(_campo("id"))
            tab.append(_campo("uuid"))
            tab.append(_campo("motivo"))
            sink, dest = self.parameterAsSink(parameters, self.LISTA, context, tab,
                                              QgsWkbTypes.NoGeometry, camada.crs())
            if sink is not None:
                for f, motivo in falhas:
                    fid, idv, uid = ident(f)
                    linha = QgsFeature(tab)
                    try:
                        fid = int(fid)
                    except ValueError:
                        fid = f.id()
                    linha.setAttributes([fid, idv or None, uid or None, motivo])
                    sink.addFeature(linha, QgsFeatureSink.FastInsert)
                resultado[self.LISTA] = dest
        return resultado
