# -*- coding: utf-8 -*-
"""Calcular Testadas por Via (Faces Colineares e Esquinas).

Para cada lote, acha as faces alinhadas com o eixo de alguma via e voltadas
para ela, e gera uma testada para cada via de frente: testada 1, 2, 3...
(lote de esquina, lote que vai de uma rua a outra). O logradouro cadastrado
no lote não é usado.

Uma face entra na testada de uma via quando:
  1. o meio dela está a até 'Tolerância' metros do eixo;
  2. ela é paralela ao trecho do eixo em frente a ela (até 'Ângulo máximo');
  3. ela está voltada para o eixo: a linha do meio da face até o eixo não
     atravessa o próprio lote (descarta o fundo) nem outro lote (descarta
     partes do lote escondidas atrás do vizinho).
Faces de via com o mesmo nome e na mesma direção formam uma testada só: eixo
partido em vários trechos não divide a testada, e duas ruas diferentes com o
mesmo nome (ex.: "SEM DENOMINAÇÃO OFICIAL") numa esquina não se juntam.
Sem nenhuma face alinhada (ex.: lote no fim da rua), vale a face mais próxima
de uma via, se o caminho até ela estiver livre.

Precisão (precisao_testada nos lotes, precisao nas linhas):
  ALTA         faces alinhadas ao eixo;
  MÉDIA        face mais próxima (nenhuma face alinhada);
  SEM TESTADA  nenhuma face a até 'Tolerância' metros de uma via, com caminho livre.
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

    CAMPO_NOME_VIA = 'Nome'
    CAMPO_UUID = 'uuid'
    CAMPO_PRECISAO = 'precisao_testada'
    CAMPOS_RESULTADO = ('testada', 'precisao_testada', 'obs_testada')  # de rodadas anteriores: não copiar

    OBSTRUCAO_MAX = 0.50    # quanto o caminho face -> eixo pode cruzar um lote (frestas de digitalização)
    FACE_MINIMA = 0.20      # regra da face mais próxima (igual à ferramenta antiga): descarta chanfros
    DIRECAO_MAX = 45.0      # faces da mesma via com direções mais diferentes que isso são frentes distintas

    ALTA, MEDIA, SEM_TESTADA = 'ALTA', 'MÉDIA', 'SEM TESTADA'

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return CalculadoraTestadasColinearesQGIS()

    def name(self):
        return 'calcular_testadas_logradouro_colineares'

    def displayName(self):
        return self.tr('Calcular Testadas por Via (Faces Colineares e Esquinas)')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr(
            """
            Gera uma testada para cada via de frente do lote: testada 1, 2, 3... (da maior para a menor),
            para lotes de esquina ou que vão de uma rua a outra. O logradouro cadastrado no lote não é usado.

            Uma face entra na testada de uma via quando:
            • o meio dela está a até 'Tolerância' metros do eixo;
            • ela é paralela ao trecho do eixo em frente a ela (até 'Ângulo máximo');
            • ela está voltada para o eixo (o caminho até o eixo não cruza o próprio lote nem outro lote).
            Faces de via com o mesmo nome e na mesma direção somam numa testada só.

            Lotes: testada_1, testada_2... e precisao_testada.
            Linhas: uma feição por testada, com lote_uuid, testada_num, via, comprimento e precisao.
            Precisão: ALTA (faces alinhadas ao eixo), MÉDIA (nenhuma face alinhada, ex.: lote no fim da rua:
            vale a face mais próxima), SEM TESTADA (nenhuma via perto com caminho livre).
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

        campo_nome_via = self._encontrar_campo(vias_layer, self.CAMPO_NOME_VIA)
        if not campo_nome_via:
            raise QgsProcessingException(f'Campo "{self.CAMPO_NOME_VIA}" não encontrado na camada de vias.')
        campo_uuid = self._encontrar_campo(lotes_layer, self.CAMPO_UUID)
        if not campo_uuid:
            feedback.pushWarning(f'Campo "{self.CAMPO_UUID}" não encontrado nos lotes: lote_uuid fica vazio.')

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

        # Vias: geometria, nome normalizado (para juntar trechos) e nome original
        vias = {'geom': {}, 'nome': {}, 'original': {}}
        indice_vias = QgsSpatialIndex()
        for via_feat in vias_layer.getFeatures():
            g = via_feat.geometry()
            if g is None or g.isEmpty():
                continue
            vid = via_feat.id()
            vias['geom'][vid] = g
            vias['nome'][vid] = self._normalizar_nome(via_feat.attribute(campo_nome_via))
            vias['original'][vid] = str(via_feat.attribute(campo_nome_via) or '').strip()
            indice_vias.addFeature(vid, g.boundingBox())

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

        # 1ª passada: testadas de cada lote (o nº de campos testada_N depende do resultado)
        resultados = []
        invalidas = 0
        for idx, g in enumerate(geoms_lotes):
            if feedback.isCanceled():
                break
            if total_lotes > 0:
                feedback.setProgress(int((idx / total_lotes) * 90))
            if g is None:
                invalidas += 1
                resultados.append(([], self.SEM_TESTADA))
            else:
                resultados.append(self._testadas_do_lote(g, vias, indice_vias, tolerancia, angulo_max,
                                                         geoms_lotes, indice_lotes))
        n_testadas = max([len(t) for t, _ in resultados] + [1])

        # Campos da camada de saída (resultados de rodadas anteriores não são copiados)
        original_fields = lotes_layer.fields()
        copiar = [f.name() for f in original_fields
                  if f.name().lower() not in self.CAMPOS_RESULTADO and not re.fullmatch(r'testada_\d+', f.name().lower())]
        fields = QgsFields()
        for field in original_fields:
            if field.name() in copiar:
                fields.append(field)
        for n in range(1, n_testadas + 1):
            fields.append(QgsField(f'testada_{n}', QVariant.Double, 'double', 10, 2))
        fields.append(QgsField(self.CAMPO_PRECISAO, QVariant.String))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, lotes_layer.wkbType(), lotes_layer.crs()
        )

        linha_sink, linha_dest_id = None, None
        if self.OUTPUT_LINHAS in parameters and parameters[self.OUTPUT_LINHAS]:
            linha_fields = QgsFields()
            linha_fields.append(QgsField('lote_fid', QVariant.Int))
            linha_fields.append(QgsField('lote_uuid', QVariant.String))
            linha_fields.append(QgsField('testada_num', QVariant.Int))
            linha_fields.append(QgsField('via', QVariant.String))
            linha_fields.append(QgsField('comprimento', QVariant.Double, 'double', 10, 2))
            linha_fields.append(QgsField('distancia_via', QVariant.Double, 'double', 10, 2))
            linha_fields.append(QgsField('precisao', QVariant.String))

            linha_sink, linha_dest_id = self.parameterAsSink(
                parameters, self.OUTPUT_LINHAS, context, linha_fields,
                QgsWkbTypes.MultiLineString, lotes_layer.crs()
            )

        # 2ª passada: grava lotes e linhas
        for lote_feature, (testadas, precisao) in zip(lotes_features, resultados):
            out_feature = QgsFeature(fields)
            out_feature.setGeometry(lote_feature.geometry())
            for nome in copiar:
                out_feature.setAttribute(nome, lote_feature.attribute(nome))
            for n, (comprimento, _, _, _) in enumerate(testadas, 1):
                out_feature.setAttribute(f'testada_{n}', comprimento)
            out_feature.setAttribute(self.CAMPO_PRECISAO, precisao)
            sink.addFeature(out_feature, QgsFeatureSink.FastInsert)

            if linha_sink:
                uuid = lote_feature.attribute(campo_uuid) if campo_uuid else None
                for n, (comprimento, linha, distancia, via) in enumerate(testadas, 1):
                    linha.convertToMultiType()
                    linha_feat = QgsFeature(linha_fields)
                    linha_feat.setGeometry(linha)
                    linha_feat.setAttribute('lote_fid', lote_feature.id())
                    linha_feat.setAttribute('lote_uuid', uuid)
                    linha_feat.setAttribute('testada_num', n)
                    linha_feat.setAttribute('via', via)
                    linha_feat.setAttribute('comprimento', comprimento)
                    linha_feat.setAttribute('distancia_via', distancia)
                    linha_feat.setAttribute('precisao', precisao)
                    linha_sink.addFeature(linha_feat, QgsFeatureSink.FastInsert)

        # Relatório Final
        contagem = {p: sum(1 for _, q in resultados if q == p) for p in (self.ALTA, self.MEDIA, self.SEM_TESTADA)}
        feedback.pushInfo('=' * 50)
        feedback.pushInfo(f'Total de lotes processados: {len(resultados)}')
        feedback.pushInfo(f'Testada ALTA  (faces alinhadas ao eixo): {contagem[self.ALTA]}')
        feedback.pushInfo(f'Testada MÉDIA (face mais próxima, nenhuma alinhada): {contagem[self.MEDIA]}')
        feedback.pushInfo(f'SEM TESTADA: {contagem[self.SEM_TESTADA]}')
        feedback.pushInfo(f'Lotes com 2 ou mais testadas (esquina / duas frentes): '
                          f'{sum(1 for t, _ in resultados if len(t) > 1)}')
        if invalidas:
            feedback.reportError(f'Lotes com geometria impossível de corrigir: {invalidas}')
        feedback.pushInfo('=' * 50)

        result = {self.OUTPUT: dest_id}
        if linha_dest_id:
            result[self.OUTPUT_LINHAS] = linha_dest_id
        return result

    # ------------------------------------------------------------------ testadas
    @staticmethod
    def _aneis_externos(lote_geom):
        poligonos = lote_geom.asMultiPolygon() if lote_geom.isMultipart() else [lote_geom.asPolygon()]
        return [poly[0] for poly in poligonos if poly and len(poly[0]) >= 4]

    def _testadas_do_lote(self, lote, vias, indice_vias, tolerancia, angulo_max, geoms_lotes, indice_lotes):
        """([(comprimento, linha, distância, via)] da maior para a menor, precisão)."""
        caixa = lote.boundingBox()
        caixa.grow(tolerancia)
        candidatas = [(v, vias['geom'][v]) for v in indice_vias.intersects(caixa)
                      if lote.distance(vias['geom'][v]) <= tolerancia]
        if not candidatas:
            return [], self.SEM_TESTADA

        aceitas = []  # (face, distância, id da via)
        for anel in self._aneis_externos(lote):
            for p1, p2 in zip(anel, anel[1:]):
                if p1 == p2:
                    continue
                face = QgsGeometry.fromPolylineXY([p1, p2])
                r = self._via_da_face(face, candidatas, tolerancia, angulo_max, geoms_lotes, indice_lotes)
                if r:
                    aceitas.append((face,) + r)
        if aceitas:
            return self._agrupar_por_via(aceitas, vias), self.ALTA

        # nenhuma face alinhada (ex.: lote no fim da rua, de frente para a ponta do eixo)
        proxima = self._face_mais_proxima(lote, candidatas, tolerancia, geoms_lotes, indice_lotes)
        if proxima:
            face, dist, vid = proxima
            return [(round(face.length(), 2), face, round(dist, 2), vias['original'][vid])], self.MEDIA
        return [], self.SEM_TESTADA

    def _via_da_face(self, face, candidatas, tolerancia, angulo_max, geoms_lotes, indice_lotes):
        """A via mais próxima de que a face é testada: (distância, id da via), ou None."""
        meio = face.interpolate(face.length() / 2.0).asPoint()
        a, b = QgsPointXY(face.vertexAt(0)), QgsPointXY(face.vertexAt(1))
        opcoes = []
        for vid, via in candidatas:
            sqr, ponto, apos, _ = via.closestSegmentWithContext(meio)
            if 0 <= sqr <= tolerancia ** 2 and apos >= 1:
                opcoes.append((sqr, vid, via, ponto))
        for sqr, vid, via, ponto in sorted(opcoes, key=lambda o: o[0]):
            # paralela ao eixo no trecho em frente à face: corda entre as projeções das pontas
            # da face no eixo (em curva, o segmento do eixo no meio da face pode ser bem mais curto)
            c = via.closestSegmentWithContext(a)[1]
            d = via.closestSegmentWithContext(b)[1]
            if self._angulo(a, b, c, d) > angulo_max:
                continue
            # voltada para o eixo: o caminho até ele não cruza o próprio lote nem outro lote
            if sqr > 0 and self._caminho_obstruido(meio, ponto, geoms_lotes, indice_lotes):
                continue
            return math.sqrt(sqr), vid
        return None

    def _agrupar_por_via(self, aceitas, vias):
        """Uma testada por via de frente: mesmo nome de via e mesma direção (esquina muda a direção)."""
        grupos = []
        for face, dist, vid in aceitas:
            nome, direcao = vias['nome'][vid], self._direcao(face)
            iguais = [g for g in grupos if g['nome'] == nome
                      and any(self._diferenca(direcao, x) <= self.DIRECAO_MAX for x in g['direcoes'])]
            if not iguais:
                iguais = [{'nome': nome, 'via': vias['original'][vid], 'direcoes': [], 'faces': [], 'dist': []}]
                grupos.append(iguais[0])
            grupo = iguais[0]
            for outro in iguais[1:]:  # a face ligou dois grupos (ex.: curva que começa no meio do anel)
                for chave in ('direcoes', 'faces', 'dist'):
                    grupo[chave] += outro[chave]
                grupos.remove(outro)
            grupo['direcoes'].append(direcao)
            grupo['faces'].append(face)
            grupo['dist'].append(dist)

        testadas = [(round(sum(f.length() for f in g['faces']), 2),
                     QgsGeometry.collectGeometry(g['faces']).mergeLines(),
                     round(min(g['dist']), 2), g['via']) for g in grupos]
        return sorted(testadas, key=lambda t: -t[0])

    def _face_mais_proxima(self, lote, candidatas, tolerancia, geoms_lotes, indice_lotes):
        """Face cujo meio está mais perto de uma via, com caminho livre: (face, distância, id da via)."""
        melhor = None
        for anel in self._aneis_externos(lote):
            for p1, p2 in zip(anel, anel[1:]):
                face = QgsGeometry.fromPolylineXY([p1, p2])
                if face.length() < self.FACE_MINIMA:
                    continue
                meio = face.interpolate(face.length() / 2.0).asPoint()
                for vid, via in candidatas:
                    sqr, ponto, _, _ = via.closestSegmentWithContext(meio)
                    if not 0 <= sqr <= tolerancia ** 2 or (melhor and sqr >= melhor[0]):
                        continue
                    if sqr > 0 and self._caminho_obstruido(meio, ponto, geoms_lotes, indice_lotes):
                        continue
                    melhor = (sqr, face, vid)
        return (melhor[1], math.sqrt(melhor[0]), melhor[2]) if melhor else None

    @staticmethod
    def _direcao(face):
        """Direção da face em graus, 0-180 (sem sentido)."""
        a, b = face.vertexAt(0), face.vertexAt(1)
        return math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())) % 180.0

    @staticmethod
    def _diferenca(a, b):
        d = abs(a - b) % 180.0
        return min(d, 180.0 - d)

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
