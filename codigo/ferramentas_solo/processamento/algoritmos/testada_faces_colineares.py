# -*- coding: utf-8 -*-
"""Calcular Testadas pelo Logradouro do Lote (Faces Colineares).

Versão do 'testada_logradouro.py' que, em vez de pegar só a face mais próxima
da via, pega TODAS as faces do lote alinhadas com o eixo do logradouro e
voltadas para ele. A testada é a soma dessas faces.

Uma face do lote entra na testada quando:
  1. o meio dela está a até 'Tolerância' metros do eixo;
  2. ela é paralela ao trecho do eixo em frente a ela (até 'Ângulo máximo');
  3. ela está voltada para o eixo: a linha do meio da face até o eixo não
     atravessa o próprio lote (descarta o fundo) nem outro lote (descarta
     partes do lote escondidas atrás do vizinho).
Se nenhuma face passar, a face mais próxima do eixo vale (regra antiga).

Quando o eixo do logradouro não serve, a testada é calculada mesmo assim,
com precisão menor, e os campos 'precisao_testada' e 'obs_testada' dizem
como ela foi obtida:
  ALTA        faces alinhadas ao eixo do logradouro do lote;
  MÉDIA       face mais próxima do eixo do logradouro (nenhuma face alinhada);
  BAIXA       eixo do logradouro até 2x a tolerância, ignorando outros lotes
              no caminho; ou a via mais próxima do lote, de qualquer nome
              (lote sem logradouro, nome sem eixo, eixo do logradouro longe);
  SEM TESTADA nenhuma via perto o bastante.
"""

import math
import re
import unicodedata
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer,
    QgsSpatialIndex,
    QgsWkbTypes,
)
import processing


class CalculadoraTestadasColinearesQGIS(QgsProcessingAlgorithm):

    INPUT_LOTES = 'INPUT_LOTES'
    INPUT_VIAS = 'INPUT_VIAS'
    TOLERANCIA = 'TOLERANCIA'
    ANGULO_MAX = 'ANGULO_MAX'
    OUTPUT = 'OUTPUT'
    OUTPUT_LINHAS = 'OUTPUT_LINHAS'

    CAMPO_LOGRADOURO_LOTE = 'logradouro'
    CAMPO_NOME_VIA = 'Nome'
    CAMPO_TESTADA = 'testada'
    CAMPO_PRECISAO = 'precisao_testada'
    CAMPO_OBS = 'obs_testada'

    OBSTRUCAO_MAX = 0.50    # quanto o caminho face -> eixo pode cruzar um lote (frestas de digitalização)
    FACE_MINIMA = 0.20      # regra da face mais próxima (igual à ferramenta antiga): descarta chanfros
    FATOR_TOLERANCIA_BAIXA = 2.0  # até onde o eixo do logradouro ainda vale, com precisão BAIXA

    ALTA, MEDIA, BAIXA, SEM_TESTADA = 'ALTA', 'MÉDIA', 'BAIXA', 'SEM TESTADA'
    REGRA_ALINHADAS, REGRA_MAIS_PROXIMA = 'faces alinhadas', 'face mais próxima'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CalculadoraTestadasColinearesQGIS()

    def name(self):
        return 'calcular_testadas_logradouro_colineares'

    def displayName(self):
        return self.tr('Calcular Testadas pelo Logradouro do Lote (Faces Colineares)')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr(
            """
            Calcula a testada dos lotes usando o logradouro cadastrado como referência,
            somando TODAS as faces do lote alinhadas com o eixo da via (não só a mais próxima).

            Uma face entra na testada quando:
            • o meio dela está a até 'Tolerância' metros do eixo;
            • ela é paralela ao trecho do eixo em frente a ela (até 'Ângulo máximo');
            • ela está voltada para o eixo (o caminho até o eixo não cruza o próprio lote nem outro lote).

            Funciona para frentes com vários vértices e para frentes em curva.

            Os campos 'precisao_testada' e 'obs_testada' dizem como a testada foi obtida:
            • ALTA: faces alinhadas ao eixo do logradouro do lote;
            • MÉDIA: face mais próxima do eixo do logradouro (nenhuma face alinhada, ex.: lote no fim da rua);
            • BAIXA: eixo do logradouro até 2x a tolerância, ignorando outros lotes no caminho, ou a via mais
              próxima do lote, de qualquer nome (lote sem logradouro, nome sem eixo, eixo longe) — confira;
            • SEM TESTADA: nenhuma via perto o bastante.
            A camada de linhas mostra as faces usadas.
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
            QgsProcessingParameterNumber(
                self.ANGULO_MAX,
                self.tr('Ângulo máximo entre a face e o eixo (graus)'),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=20.0,
                minValue=0.0,
                maxValue=60.0
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

    # ------------------------------------------------------------------ utilitários
    def _normalizar_nome(self, valor):
        if valor is None or str(valor) == 'NULL':   # campo vazio chega como QVariant nulo, não None
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

    @staticmethod
    def _geometria_valida(geom):
        """Corrige o lote inválido em vez de pular (mesma ideia do Gerar / Atualizar Quadras)."""
        if geom is None or geom.isEmpty():
            return None
        if geom.isGeosValid():
            return geom
        g = geom.makeValid()
        if g is None or g.isEmpty():
            return None
        if QgsWkbTypes.flatType(g.wkbType()) == QgsWkbTypes.GeometryCollection:
            polys = [p for p in g.asGeometryCollection()
                     if QgsWkbTypes.geometryType(p.wkbType()) == QgsWkbTypes.PolygonGeometry]
            g = QgsGeometry.unaryUnion(polys) if polys else None
        return g

    # ------------------------------------------------------------------ execução
    def processAlgorithm(self, parameters, context, feedback):
        lotes_layer = self.parameterAsVectorLayer(parameters, self.INPUT_LOTES, context)
        vias_layer = self.parameterAsVectorLayer(parameters, self.INPUT_VIAS, context)
        tolerancia = self.parameterAsDouble(parameters, self.TOLERANCIA, context)
        angulo_max = self.parameterAsDouble(parameters, self.ANGULO_MAX, context)

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

        # Campos da camada de saída (os campos de resultado substituem os de mesmo nome)
        novos = (self.CAMPO_TESTADA, self.CAMPO_PRECISAO, self.CAMPO_OBS)
        original_fields = lotes_layer.fields()
        copiar = [f.name() for f in original_fields if f.name().lower() not in novos]
        fields = QgsFields()
        for field in original_fields:
            if field.name() in copiar:
                fields.append(field)
        fields.append(QgsField(self.CAMPO_TESTADA, QVariant.Double, 'double', 10, 2))
        fields.append(QgsField(self.CAMPO_PRECISAO, QVariant.String))
        fields.append(QgsField(self.CAMPO_OBS, QVariant.String))

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
            linha_fields.append(QgsField('precisao', QVariant.String))
            linha_fields.append(QgsField('obs', QVariant.String))

            linha_sink, linha_dest_id = self.parameterAsSink(
                parameters, self.OUTPUT_LINHAS, context, linha_fields,
                QgsWkbTypes.MultiLineString, lotes_layer.crs()
            )

        # Vias: geometria, nome normalizado e nome original; índice para achar a via mais próxima
        vias = {'geom': {}, 'nome': {}, 'original': {}, 'por_nome': {}}
        indice_vias = QgsSpatialIndex(QgsSpatialIndex.FlagStoreFeatureGeometries)
        for via_feat in vias_layer.getFeatures():
            g = via_feat.geometry()
            if g is None or g.isEmpty():
                continue
            vid = via_feat.id()
            nome_norm = self._normalizar_nome(via_feat.attribute(campo_nome_via))
            vias['geom'][vid] = g
            vias['nome'][vid] = nome_norm
            vias['original'][vid] = str(via_feat.attribute(campo_nome_via) or '').strip()
            if nome_norm:
                vias['por_nome'].setdefault(nome_norm, []).append(vid)
            indice_vias.addFeature(via_feat)

        # Lotes (geometria corrigida) + índice espacial, para saber se uma face está escondida
        lotes_features = list(lotes_layer.getFeatures())
        total_lotes = len(lotes_features)
        geoms_lotes = []
        indice_lotes = QgsSpatialIndex()
        for i, f in enumerate(lotes_features):
            g = self._geometria_valida(f.geometry())
            geoms_lotes.append(g)
            if g is not None:
                indice_lotes.addFeature(i, g.boundingBox())

        contagem = {self.ALTA: 0, self.MEDIA: 0, self.BAIXA: 0, self.SEM_TESTADA: 0}
        estatisticas = {'sem_logradouro': 0, 'logradouro_nao_encontrado': 0, 'geometria_invalida': 0}

        for idx, lote_feature in enumerate(lotes_features):
            if feedback.isCanceled():
                break

            if total_lotes > 0:
                feedback.setProgress(int((idx / total_lotes) * 100))

            lote_geom = lote_feature.geometry()
            lote_calc = geoms_lotes[idx]
            valor_logradouro = lote_feature.attribute(campo_logradouro)
            logradouro_norm = self._normalizar_nome(valor_logradouro)

            if not logradouro_norm:
                estatisticas['sem_logradouro'] += 1
            elif logradouro_norm not in vias['por_nome']:
                estatisticas['logradouro_nao_encontrado'] += 1

            if lote_calc is None:
                estatisticas['geometria_invalida'] += 1
                resultado, precisao, obs = None, self.SEM_TESTADA, 'geometria inválida'
            else:
                resultado, precisao, obs = self._testada_em_cascata(
                    lote_calc, logradouro_norm, vias, indice_vias, tolerancia, angulo_max,
                    geoms_lotes, indice_lotes)

            comprimento_testada, testada_geom, distancia_via = resultado or (0.0, None, 0.0)
            contagem[precisao] += 1

            # Grava feição do lote no Sink
            out_feature = QgsFeature(fields)
            out_feature.setGeometry(lote_geom)
            for nome in copiar:
                out_feature.setAttribute(nome, lote_feature.attribute(nome))
            out_feature.setAttribute(self.CAMPO_TESTADA, comprimento_testada)
            out_feature.setAttribute(self.CAMPO_PRECISAO, precisao)
            out_feature.setAttribute(self.CAMPO_OBS, obs)
            sink.addFeature(out_feature, QgsFeatureSink.FastInsert)

            # Grava feição de linha no Sink (se habilitado)
            if linha_sink and testada_geom and comprimento_testada > 0:
                testada_geom.convertToMultiType()
                linha_feat = QgsFeature(linha_fields)
                linha_feat.setGeometry(testada_geom)
                linha_feat.setAttribute('lote_fid', lote_feature.id())
                linha_feat.setAttribute('logradouro', valor_logradouro)
                linha_feat.setAttribute('comprimento', comprimento_testada)
                linha_feat.setAttribute('distancia_via', distancia_via)
                linha_feat.setAttribute('precisao', precisao)
                linha_feat.setAttribute('obs', obs)
                linha_sink.addFeature(linha_feat, QgsFeatureSink.FastInsert)

        # Relatório Final
        feedback.pushInfo('=' * 50)
        feedback.pushInfo(f'Total de lotes processados: {sum(contagem.values())}')
        feedback.pushInfo(f'Testada ALTA  (faces alinhadas ao eixo do logradouro): {contagem[self.ALTA]}')
        feedback.pushInfo(f'Testada MÉDIA (face mais próxima do eixo do logradouro): {contagem[self.MEDIA]}')
        feedback.pushInfo(f'Testada BAIXA (eixo longe/atrás de lote ou via mais próxima, confira): {contagem[self.BAIXA]}')
        feedback.pushInfo(f'SEM TESTADA: {contagem[self.SEM_TESTADA]}')
        feedback.pushInfo(f'Lotes sem logradouro preenchido: {estatisticas["sem_logradouro"]}')
        feedback.pushInfo(f'Logradouros não localizados nas vias: {estatisticas["logradouro_nao_encontrado"]}')
        if estatisticas['geometria_invalida']:
            feedback.reportError(f'Lotes com geometria impossível de corrigir: {estatisticas["geometria_invalida"]}')
        feedback.pushInfo('=' * 50)

        result = {self.OUTPUT: dest_id}
        if linha_dest_id:
            result[self.OUTPUT_LINHAS] = linha_dest_id
        return result

    # ------------------------------------------------------------------ cascata de precisão
    def _testada_em_cascata(self, lote, nome, vias, indice_vias, tolerancia, angulo_max,
                            geoms_lotes, indice_lotes):
        """Retorna ((comprimento, linha, distância) ou None, precisão, observação)."""
        ids_logradouro = vias['por_nome'].get(nome, [])
        dist_logradouro = min((lote.distance(vias['geom'][v]) for v in ids_logradouro), default=None)

        # 1) eixo do logradouro do lote, dentro da tolerância
        perto = [vias['geom'][v] for v in ids_logradouro if lote.distance(vias['geom'][v]) <= tolerancia]
        if perto:
            r = self._calcular_testada_lote(lote, perto, tolerancia, angulo_max, geoms_lotes, indice_lotes)
            if r and r[3] == self.REGRA_ALINHADAS:
                return r[:3], self.ALTA, 'faces alinhadas ao eixo do logradouro'
            if r:
                return r[:3], self.MEDIA, 'face mais próxima do eixo do logradouro (nenhuma face alinhada)'

        # 2) eixo do logradouro mais longe, ou atrás de outro lote: só o próprio lote bloqueia
        tol_baixa = tolerancia * self.FATOR_TOLERANCIA_BAIXA
        longe = [vias['geom'][v] for v in ids_logradouro if lote.distance(vias['geom'][v]) <= tol_baixa]
        if longe:
            so_o_lote = QgsSpatialIndex()
            so_o_lote.addFeature(0, lote.boundingBox())
            r = self._calcular_testada_lote(lote, longe, tol_baixa, angulo_max, [lote], so_o_lote)
            if r:
                return (r[:3], self.BAIXA,
                        f'eixo do logradouro a {r[2]:.1f} m: além da tolerância ou atrás de outro lote')

        # 3) via mais próxima do lote, de qualquer nome
        if not nome:
            motivo = 'lote sem logradouro'
        elif not ids_logradouro:
            motivo = 'logradouro sem eixo com esse nome'
        else:
            motivo = f'eixo do logradouro a {dist_logradouro:.1f} m'
        proxima = indice_vias.nearestNeighbor(lote, 1, tolerancia)
        if proxima:
            vid = proxima[0]
            mesmo_nome = vias['por_nome'].get(vias['nome'][vid], [vid])
            perto = [vias['geom'][v] for v in mesmo_nome if lote.distance(vias['geom'][v]) <= tolerancia]
            r = self._calcular_testada_lote(lote, perto, tolerancia, angulo_max, geoms_lotes, indice_lotes)
            if r:
                via = vias['original'][vid] or '(via sem nome)'
                return r[:3], self.BAIXA, f'{motivo}; usada a via mais próxima: {via}'

        return None, self.SEM_TESTADA, f'{motivo}; nenhuma via a até {tolerancia:.0f} m'

    # ------------------------------------------------------------------ testada
    @staticmethod
    def _aneis_externos(lote_geom):
        poligonos = lote_geom.asMultiPolygon() if lote_geom.isMultipart() else [lote_geom.asPolygon()]
        return [poly[0] for poly in poligonos if poly and len(poly[0]) >= 4]

    def _calcular_testada_lote(self, lote_geom, vias, tolerancia, angulo_max, geoms_lotes, indice_lotes):
        escolhidas, distancias = [], []
        for anel in self._aneis_externos(lote_geom):
            for p1, p2 in zip(anel, anel[1:]):
                if p1 == p2:
                    continue
                face = QgsGeometry.fromPolylineXY([p1, p2])
                dist = self._avaliar_face(face, vias, tolerancia, angulo_max, geoms_lotes, indice_lotes)
                if dist is not None:
                    escolhidas.append(face)
                    distancias.append(dist)

        if not escolhidas:
            # nenhuma face alinhada (ex.: lote no fim da rua, de frente para a ponta do eixo)
            return self._face_mais_proxima(lote_geom, vias, tolerancia)

        comprimento = sum(f.length() for f in escolhidas)
        linha = QgsGeometry.collectGeometry(escolhidas).mergeLines()
        return (round(comprimento, 2), linha, round(min(distancias), 2), self.REGRA_ALINHADAS)

    def _face_mais_proxima(self, lote_geom, vias, tolerancia):
        """Regra da ferramenta antiga: a face cujo meio está mais perto do eixo."""
        melhor = None
        for anel in self._aneis_externos(lote_geom):
            for p1, p2 in zip(anel, anel[1:]):
                face = QgsGeometry.fromPolylineXY([p1, p2])
                if face.length() < self.FACE_MINIMA:
                    continue
                meio = face.interpolate(face.length() / 2.0)
                dist = min(meio.distance(v) for v in vias)
                if dist <= tolerancia and (melhor is None or dist < melhor[0]):
                    melhor = (dist, face)
        if melhor is None:
            return None
        dist, face = melhor
        return (round(face.length(), 2), face, round(dist, 2), self.REGRA_MAIS_PROXIMA)

    def _avaliar_face(self, face, vias, tolerancia, angulo_max, geoms_lotes, indice_lotes):
        """Retorna a distância da face ao eixo se ela for testada, senão None."""
        meio = face.interpolate(face.length() / 2.0).asPoint()

        # trecho de eixo mais próximo do meio da face
        melhor = None
        for via in vias:
            sqr, ponto, apos, _ = via.closestSegmentWithContext(meio)
            if sqr >= 0 and apos >= 1 and (melhor is None or sqr < melhor[0]):
                melhor = (sqr, ponto, apos, via)
        if melhor is None:
            return None

        sqr, ponto_eixo, apos, via = melhor
        dist = math.sqrt(sqr)
        if dist > tolerancia:
            return None

        # paralela ao eixo no trecho em frente à face: corda entre as projeções das pontas
        # da face no eixo (em curva, o segmento do eixo no meio da face pode ser bem mais curto)
        a, b = QgsPointXY(face.vertexAt(0)), QgsPointXY(face.vertexAt(1))
        c = via.closestSegmentWithContext(a)[1]
        d = via.closestSegmentWithContext(b)[1]
        if self._angulo(a, b, c, d) > angulo_max:
            return None

        # voltada para o eixo: o caminho até ele não cruza o próprio lote nem outro lote
        if dist > 0 and self._caminho_obstruido(meio, ponto_eixo, geoms_lotes, indice_lotes):
            return None

        return dist

    @staticmethod
    def _angulo(a, b, c, d):
        """Ângulo (0-90°) entre os segmentos a-b e c-d."""
        ux, uy = b.x() - a.x(), b.y() - a.y()
        vx, vy = d.x() - c.x(), d.y() - c.y()
        nu, nv = math.hypot(ux, uy), math.hypot(vx, vy)
        if nu == 0 or nv == 0:
            return 90.0
        return math.degrees(math.acos(min(1.0, abs(ux * vx + uy * vy) / (nu * nv))))

    def _caminho_obstruido(self, meio, ponto_eixo, geoms_lotes, indice_lotes):
        caminho = QgsGeometry.fromPolylineXY([meio, ponto_eixo])
        caixa = caminho.boundingBox()
        caixa.grow(0.01)
        for j in indice_lotes.intersects(caixa):
            g = geoms_lotes[j]
            if g.intersects(caminho) and g.intersection(caminho).length() > self.OBSTRUCAO_MAX:
                return True
        return False
