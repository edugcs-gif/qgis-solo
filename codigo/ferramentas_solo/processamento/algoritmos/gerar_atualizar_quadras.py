# -*- coding: utf-8 -*-
"""
Gerar / Atualizar Quadras a partir dos Lotes
--------------------------------------------
1) Separa os lotes em quarteirões físicos: lotes que se tocam (ou ficam a menos da
   "distância entre lotes do mesmo quarteirão") formam um bloco contínuo.
2) Dentro de cada quarteirão, agrupa pelo campo da quadra (e campo extra, se houver).
   -> Quadras com o MESMO número em lugares diferentes viram polígonos SEPARADOS,
      sem precisar de setor.
3) Une (dissolve) os lotes de cada grupo, mantendo os vértices originais do contorno.
4) Na camada de quadras existente, casa cada polígono gerado com a quadra de mesmo
   número que mais se sobrepõe a ele; se não houver, cria uma quadra nova (opcional).
Se nenhuma camada de quadras for escolhida, cria uma camada nova.

Instalação: Caixa de Ferramentas de Processamento > ícone Python > "Adicionar script à caixa..."
"""

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFeatureSink,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsProcessingUtils,
    QgsFeature,
    QgsGeometry,
    QgsRectangle,
    QgsSpatialIndex,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant
from collections import Counter
import processing
import uuid

from ._util import sem_thread


class GerarAtualizarQuadras(QgsProcessingAlgorithm):

    LOTES = "LOTES"
    CAMPO_LOTE = "CAMPO_LOTE"
    CAMPO_LOTE_2 = "CAMPO_LOTE_2"
    QUADRAS = "QUADRAS"
    CAMPO_QUADRA = "CAMPO_QUADRA"
    CAMPO_QUADRA_2 = "CAMPO_QUADRA_2"
    TOLERANCIA = "TOLERANCIA"
    DIST_BLOCO = "DIST_BLOCO"
    INCLUIR_SEM_NUMERO = "INCLUIR_SEM_NUMERO"
    REMOVER_BURACOS = "REMOVER_BURACOS"
    REMOVER_COLINEARES = "REMOVER_COLINEARES"
    CRIAR_NOVAS = "CRIAR_NOVAS"
    NORMALIZAR = "NORMALIZAR"
    SALVAR = "SALVAR"
    SAIDA = "SAIDA"

    # ------------------------------------------------------------------ metadados
    def name(self):
        return "gerar_atualizar_quadras"

    def displayName(self):
        return "Gerar / Atualizar Quadras a partir dos Lotes"

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return (
            "Gera o polígono de cada quadra unindo os lotes.\n\n"
            "• Quadras com o mesmo número em lugares diferentes são separadas pelo quarteirão "
            "físico: cada conjunto contínuo de lotes vira um polígono próprio. Não precisa de setor.\n"
            "• Distância entre lotes do mesmo quarteirão: lotes mais próximos que isso são "
            "considerados do mesmo quarteirão. Deve ser bem menor que a largura das ruas.\n"
            "• Incluir lotes sem número: lotes sem valor no campo da quadra entram no polígono "
            "quando o quarteirão tem um único número de quadra.\n"
            "• Campo extra (opcional): quando já existir setor, use setor + quadra.\n"
            "• Na camada existente, cada polígono gerado atualiza a quadra de mesmo número que "
            "mais se sobrepõe a ele; sem sobreposição, é criada uma quadra nova.\n"
            "• As alterações ficam no modo de edição (dá para desfazer), a menos que "
            "'Salvar automaticamente' esteja marcado."
        )

    def flags(self):
        return sem_thread(super().flags())

    def createInstance(self):
        return GerarAtualizarQuadras()

    # ------------------------------------------------------------------ parâmetros
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LOTES, "Camada de lotes", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterField(
            self.CAMPO_LOTE, "Campo da quadra nos lotes", parentLayerParameterName=self.LOTES))
        self.addParameter(QgsProcessingParameterField(
            self.CAMPO_LOTE_2, "Campo extra nos lotes (opcional, ex.: setor)",
            parentLayerParameterName=self.LOTES, optional=True))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.QUADRAS, "Camada de quadras existente (deixe vazio para criar uma nova)",
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.CAMPO_QUADRA, "Campo correspondente nas quadras",
            parentLayerParameterName=self.QUADRAS, optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.CAMPO_QUADRA_2, "Campo extra nas quadras (opcional)",
            parentLayerParameterName=self.QUADRAS, optional=True))

        self.addParameter(QgsProcessingParameterNumber(
            self.TOLERANCIA, "Tolerância para aproximar vértices dos lotes (unidades do mapa)",
            QgsProcessingParameterNumber.Double, defaultValue=0.10, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DIST_BLOCO, "Distância máxima entre lotes do mesmo quarteirão (bem menor que a rua)",
            QgsProcessingParameterNumber.Double, defaultValue=1.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.INCLUIR_SEM_NUMERO,
            "Incluir lotes sem número no polígono (quando o quarteirão tem um só número)",
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.REMOVER_BURACOS, "Remover buracos internos (usar só o contorno externo)", defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.REMOVER_COLINEARES, "Remover vértices colineares do contorno", defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CRIAR_NOVAS, "Criar quadras que ainda não existem", defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.NORMALIZAR, "Comparar valores normalizados (ex.: '01' = 1)", defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SALVAR, "Salvar edições automaticamente", defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.SAIDA, "Quadras (nova camada — usada só se não houver camada existente)",
            QgsProcessing.TypeVectorPolygon, optional=True, createByDefault=True))

    # ------------------------------------------------------------------ utilitários
    @staticmethod
    def _norm(valor, normalizar):
        if valor is None:
            return None
        s = str(valor).strip()
        if s == "" or s.upper() == "NULL":
            return None
        if normalizar:
            try:
                f = float(s.replace(",", "."))
                if f.is_integer():
                    return str(int(f))
                return str(f)
            except ValueError:
                return s.upper()
        return s

    def _chave(self, feat, c1, c2, normalizar):
        v1 = self._norm(feat[c1], normalizar)
        if v1 is None:
            return None
        if c2:
            return (self._norm(feat[c2], normalizar), v1)
        return (None, v1)

    @staticmethod
    def _rotulo(chave):
        return f"{chave[0]} / {chave[1]}" if chave[0] is not None else str(chave[1])

    @staticmethod
    def _sem_buracos(geom):
        partes = []
        for poly in geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]:
            if poly:
                partes.append([poly[0]])
        if not partes:
            return geom
        if len(partes) == 1:
            return QgsGeometry.fromPolygonXY(partes[0])
        return QgsGeometry.fromMultiPolygonXY(partes)

    @staticmethod
    def _partes(geom):
        """Separa um multipolígono em polígonos simples."""
        if geom is None or geom.isEmpty():
            return []
        if not geom.isMultipart():
            return [geom]
        return [p for p in geom.asGeometryCollection() if not p.isEmpty() and p.area() > 0]

    @staticmethod
    def _agrupar_por_proximidade(geoms, dist, feedback):
        """Agrupa os lotes em quarteirões físicos (componentes conexos por distância)."""
        n = len(geoms)
        pai = list(range(n))

        def achar(i):
            while pai[i] != i:
                pai[i] = pai[pai[i]]
                i = pai[i]
            return i

        idx = QgsSpatialIndex()
        for i, g in enumerate(geoms):
            idx.addFeature(i, g.boundingBox())

        for i, g in enumerate(geoms):
            if feedback.isCanceled():
                break
            if i % 500 == 0:
                feedback.setProgress(30.0 * i / max(n, 1))
            bb = QgsRectangle(g.boundingBox())
            bb.grow(dist)
            for j in idx.intersects(bb):
                if j <= i:
                    continue
                ri, rj = achar(i), achar(j)
                if ri == rj:
                    continue
                if g.distance(geoms[j]) <= dist:
                    pai[ri] = rj

        blocos = {}
        for i in range(n):
            blocos.setdefault(achar(i), []).append(i)
        return list(blocos.values())

    # ------------------------------------------------------------------ execução
    def processAlgorithm(self, parameters, context, feedback):
        campo_lote = self.parameterAsString(parameters, self.CAMPO_LOTE, context)
        campo_lote_2 = self.parameterAsString(parameters, self.CAMPO_LOTE_2, context) or None
        quadras = self.parameterAsVectorLayer(parameters, self.QUADRAS, context)
        campo_quadra = self.parameterAsString(parameters, self.CAMPO_QUADRA, context)
        campo_quadra_2 = self.parameterAsString(parameters, self.CAMPO_QUADRA_2, context) or None
        tol = self.parameterAsDouble(parameters, self.TOLERANCIA, context)
        dist = self.parameterAsDouble(parameters, self.DIST_BLOCO, context)
        incluir_sem = self.parameterAsBool(parameters, self.INCLUIR_SEM_NUMERO, context)
        remover_buracos = self.parameterAsBool(parameters, self.REMOVER_BURACOS, context)
        remover_colin = self.parameterAsBool(parameters, self.REMOVER_COLINEARES, context)
        criar = self.parameterAsBool(parameters, self.CRIAR_NOVAS, context)
        normalizar = self.parameterAsBool(parameters, self.NORMALIZAR, context)
        salvar = self.parameterAsBool(parameters, self.SALVAR, context)

        lotes_src = self.parameterAsSource(parameters, self.LOTES, context)
        if lotes_src is None:
            raise QgsProcessingException("Camada de lotes inválida.")

        if quadras is not None:
            if not campo_quadra:
                raise QgsProcessingException("Escolha o campo correspondente na camada de quadras.")
            if bool(campo_lote_2) != bool(campo_quadra_2):
                raise QgsProcessingException(
                    "Preencha o campo extra nas duas camadas (lotes e quadras) ou em nenhuma.")
            if lotes_src.sourceCrs() != quadras.crs():
                raise QgsProcessingException(
                    "Lotes e quadras estão em SRCs diferentes. Reprojete antes de rodar.")

        # 1) aproxima vértices dos lotes (opcional) ----------------------------
        if tol > 0:
            feedback.pushInfo(f"Aproximando vértices dos lotes (tolerância {tol})...")
            res = processing.run("native:snapgeometries", {
                "INPUT": parameters[self.LOTES],
                "REFERENCE_LAYER": parameters[self.LOTES],
                "TOLERANCE": tol,
                "BEHAVIOR": 0,
                "OUTPUT": "memory:",
            }, context=context, feedback=feedback, is_child_algorithm=True)
            lotes = QgsProcessingUtils.mapLayerFromString(res["OUTPUT"], context)
        else:
            lotes = lotes_src

        # 2) lê todos os lotes (inclusive os sem número) -----------------------
        geoms, chaves, origs = [], [], []
        for f in lotes.getFeatures():
            if feedback.isCanceled():
                return {}
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            if not g.isGeosValid():
                g = self._so_poligonos(g.makeValid())
                if g is None or g.isEmpty():
                    continue
            geoms.append(g)
            chaves.append(self._chave(f, campo_lote, campo_lote_2, normalizar))
            origs.append((f[campo_lote_2] if campo_lote_2 else None, f[campo_lote]))

        sem_valor = sum(1 for c in chaves if c is None)
        if sem_valor == len(chaves):
            raise QgsProcessingException("Nenhum lote com valor no campo de agrupamento.")

        # 3) quarteirões físicos ---------------------------------------------
        feedback.pushInfo(f"Separando quarteirões (distância entre lotes ≤ {dist})...")
        blocos = self._agrupar_por_proximidade(geoms, dist, feedback)
        feedback.pushInfo(f"{len(blocos)} quarteirão(ões) contínuo(s) encontrado(s).")

        # 4) grupos (quarteirão x número da quadra) ----------------------------
        pecas = []  # (chave, valores originais, [geometrias])
        blocos_mistos, blocos_sem_num = [], 0
        sem_num_usados = sem_num_ignorados = 0
        for membros in blocos:
            por_chave, sem = {}, []
            for i in membros:
                if chaves[i] is None:
                    sem.append(i)
                else:
                    por_chave.setdefault(chaves[i], []).append(i)
            if not por_chave:
                blocos_sem_num += 1
                sem_num_ignorados += len(sem)
                continue
            if len(por_chave) > 1:
                blocos_mistos.append("+".join(sorted(self._rotulo(c) for c in por_chave)))
            for ch, idxs in por_chave.items():
                gs = [geoms[i] for i in idxs]
                if incluir_sem and sem and len(por_chave) == 1:
                    gs += [geoms[i] for i in sem]
                    sem_num_usados += len(sem)
                pecas.append((ch, origs[idxs[0]], gs))
            if sem and not (incluir_sem and len(por_chave) == 1):
                sem_num_ignorados += len(sem)

        # 5) geometria de cada quadra -----------------------------------------
        tol_fecho = max(tol, dist / 2.0)
        novas = []  # (chave, valores originais, geometria simples)
        falhas = []
        for ch, orig, gs in pecas:
            geom = self._geom_quadra(gs, remover_buracos, remover_colin, tol_fecho)
            if geom is None or geom.isEmpty():
                falhas.append(self._rotulo(ch))
                continue
            for parte in self._partes(geom):
                novas.append((ch, orig, parte))

        repetidas = Counter(self._rotulo(ch) for ch, _, _ in novas)
        repetidas = {r: n for r, n in repetidas.items() if n > 1}

        if quadras is None:
            resultado = self._criar_camada(parameters, context, feedback, lotes_src,
                                           campo_lote, campo_lote_2, novas)
        else:
            resultado = self._atualizar_camada(quadras, campo_quadra, campo_quadra_2,
                                               normalizar, criar, salvar, novas, feedback)

        # 6) relatório --------------------------------------------------------
        feedback.pushInfo("")
        feedback.pushInfo(f"Lotes sem número: {sem_valor} "
                          f"({sem_num_usados} incluídos em quadras, {sem_num_ignorados} ignorados)")
        if blocos_sem_num:
            feedback.pushInfo(f"Quarteirões só com lotes sem número (não gerados): {blocos_sem_num}")
        if repetidas:
            feedback.pushInfo("Números de quadra em mais de um lugar (viraram polígonos separados): "
                              + ", ".join(f"{r} ({n}x)" for r, n in sorted(repetidas.items())))
        if blocos_mistos:
            feedback.reportError("Quarteirões com mais de um número de quadra (confira se a rua "
                                 "não ficou 'colada' ou se há lote com número errado): "
                                 + "; ".join(blocos_mistos))
        if falhas:
            feedback.reportError("Falha ao unir os lotes: " + ", ".join(falhas))
        return resultado

    # ------------------------------------------------------------------ camada existente
    def _atualizar_camada(self, quadras, campo_quadra, campo_quadra_2, normalizar,
                          criar, salvar, novas, feedback):
        existentes = {}
        for q in quadras.getFeatures():
            ch = self._chave(q, campo_quadra, campo_quadra_2, normalizar)
            if ch is None:
                continue
            qg = q.geometry()
            if qg is not None and not qg.isEmpty() and not qg.isGeosValid():
                qg = self._so_poligonos(qg.makeValid())
            existentes.setdefault(ch, []).append((q.id(), qg))

        por_chave = {}
        for k, (ch, _, _) in enumerate(novas):
            por_chave.setdefault(ch, []).append(k)

        # casa cada polígono gerado com a quadra existente de mesmo número que mais se sobrepõe
        destino = {}
        sobras = []
        for ch, ks in por_chave.items():
            cands = existentes.get(ch, [])
            pares = []
            for k in ks:
                g = novas[k][2]
                bb = g.boundingBox()
                for fid, qg in cands:
                    if qg is None or qg.isEmpty() or not bb.intersects(qg.boundingBox()):
                        continue
                    inter = g.intersection(qg)
                    a = inter.area() if inter else 0
                    if a > 0:
                        pares.append((a, k, fid))
            pares.sort(reverse=True)
            usados_k, usados_f = set(), set()
            for a, k, fid in pares:
                if k in usados_k or fid in usados_f:
                    continue
                destino[k] = fid
                usados_k.add(k)
                usados_f.add(fid)
            livres_k = [k for k in ks if k not in usados_k]
            livres_f = [fid for fid, _ in cands if fid not in usados_f]
            # 1 gerado sem par + 1 existente sem par -> casa mesmo sem sobreposição
            if len(livres_k) == 1 and len(livres_f) == 1:
                destino[livres_k[0]] = livres_f[0]
                livres_f = []
            if livres_f:
                sobras.append(f"{self._rotulo(ch)} ({len(livres_f)})")

        ja_editando = quadras.isEditable()
        if not ja_editando and not quadras.startEditing():
            raise QgsProcessingException("Não foi possível colocar a camada de quadras em edição.")

        destino_multi = QgsWkbTypes.isMultiType(quadras.wkbType())
        i_q1 = quadras.fields().indexOf(campo_quadra)
        i_q2 = quadras.fields().indexOf(campo_quadra_2) if campo_quadra_2 else -1
        i_uuid = quadras.fields().lookupField("uuid")
        i_area = quadras.fields().lookupField("area")

        atualizadas = criadas = puladas = 0
        quadras.beginEditCommand("Gerar/Atualizar quadras a partir dos lotes")
        total = len(novas)
        for k, (ch, orig, geom) in enumerate(novas):
            if feedback.isCanceled():
                break
            feedback.setProgress(30.0 + 70.0 * k / max(total, 1))
            geom = QgsGeometry(geom)
            if destino_multi:
                geom.convertToMultiType()
            else:
                geom.convertToSingleType()

            fid = destino.get(k)
            if fid is not None:
                quadras.changeGeometry(fid, geom)
                if i_area >= 0:
                    quadras.changeAttributeValue(fid, i_area, round(geom.area(), 3))
                if i_uuid >= 0 and not self._norm(quadras.getFeature(fid)[i_uuid], False):
                    quadras.changeAttributeValue(fid, i_uuid, str(uuid.uuid4()))
                atualizadas += 1
            elif criar:
                nova = QgsFeature(quadras.fields())
                nova.setGeometry(geom)
                orig2, orig1 = orig
                nova.setAttribute(i_q1, self._valor_para_campo(quadras, i_q1, orig1))
                if i_q2 >= 0:
                    nova.setAttribute(i_q2, self._valor_para_campo(quadras, i_q2, orig2))
                if i_uuid >= 0:
                    nova.setAttribute(i_uuid, str(uuid.uuid4()))
                if i_area >= 0:
                    nova.setAttribute(i_area, round(geom.area(), 3))
                quadras.addFeature(nova)
                criadas += 1
            else:
                puladas += 1
        quadras.endEditCommand()

        if salvar and not ja_editando:
            if not quadras.commitChanges():
                raise QgsProcessingException("Erro ao salvar: " + "; ".join(quadras.commitErrors()))
        quadras.triggerRepaint()

        feedback.pushInfo("")
        feedback.pushInfo(f"Quadras atualizadas: {atualizadas}")
        feedback.pushInfo(f"Quadras criadas:     {criadas}")
        feedback.pushInfo(f"Não criadas:         {puladas}")
        if sobras:
            feedback.reportError("Quadras existentes que não casaram com nenhum polígono gerado "
                                 "(ficaram como estavam — confira/apague): " + ", ".join(sobras))
        if not salvar:
            feedback.pushInfo("Edições pendentes na camada de quadras — revise e salve (ou desfaça com Ctrl+Z).")
        return {"ATUALIZADAS": atualizadas, "CRIADAS": criadas, "IGNORADAS": puladas}

    # ------------------------------------------------------------------ geometria
    @staticmethod
    def _buffer_quadrado(geom, dist):
        try:  # QGIS >= 3.30
            from qgis.core import Qgis
            return geom.buffer(dist, 1, Qgis.EndCapStyle.Square, Qgis.JoinStyle.Miter, 10.0)
        except (AttributeError, TypeError):
            return geom.buffer(dist, 1, QgsGeometry.CapSquare, QgsGeometry.JoinStyleMiter, 10.0)

    @staticmethod
    def _so_poligonos(geom):
        if geom is None or geom.isEmpty():
            return geom
        if QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PolygonGeometry \
                and QgsWkbTypes.flatType(geom.wkbType()) != QgsWkbTypes.GeometryCollection:
            return geom
        polys = [g for g in geom.asGeometryCollection()
                 if QgsWkbTypes.geometryType(g.wkbType()) == QgsWkbTypes.PolygonGeometry]
        return QgsGeometry.unaryUnion(polys) if polys else QgsGeometry()

    def _fechar_frestas(self, geom, geoms_lotes, tol):
        """Fechamento (buffer +tol / -tol em quina) e volta dos vértices para os dos lotes."""
        fechado = self._buffer_quadrado(self._buffer_quadrado(geom, tol), -tol)
        fechado = self._so_poligonos(fechado)
        if fechado is None or fechado.isEmpty():
            return geom

        pts = [(v.x(), v.y()) for g in geoms_lotes for v in g.vertices()]
        if not pts:
            return fechado

        lim = (tol * 2.0) ** 2
        mover = []
        for i, v in enumerate(fechado.vertices()):
            x, y = v.x(), v.y()
            melhor, dmin = None, lim
            for px, py in pts:
                d = (px - x) ** 2 + (py - y) ** 2
                if d < dmin:
                    melhor, dmin = (px, py), d
                    if d == 0:
                        break
            if melhor and dmin > 0:
                mover.append((i, melhor))
        for i, (px, py) in mover:
            fechado.moveVertex(px, py, i)

        fechado.removeDuplicateNodes(tol / 10.0)
        if not fechado.isGeosValid():
            fechado = self._so_poligonos(fechado.makeValid())
        return fechado

    def _geom_quadra(self, geoms, remover_buracos, remover_colin, tol=0.0):
        geom = QgsGeometry.unaryUnion(geoms)
        if geom is None or geom.isEmpty():
            return None
        if not geom.isGeosValid():
            geom = self._so_poligonos(geom.makeValid())
        if tol > 0:
            geom = self._fechar_frestas(geom, geoms, tol)
        if remover_buracos:
            geom = self._sem_buracos(geom)
        if remover_colin:
            geom = geom.simplify(1e-9)
        geom.removeDuplicateNodes()
        return geom

    # ------------------------------------------------------------------ camada nova
    def _criar_camada(self, parameters, context, feedback, lotes_src,
                      campo_lote, campo_lote_2, novas):
        campos = QgsFields()
        campos.append(QgsField("uuid", QVariant.String, "string", 36))
        if campo_lote_2:
            campos.append(QgsField(lotes_src.fields().field(campo_lote_2)))
        campos.append(QgsField(lotes_src.fields().field(campo_lote)))
        campos.append(QgsField("area", QVariant.Double, "double", 20, 3))

        sink, dest_id = self.parameterAsSink(
            parameters, self.SAIDA, context, campos,
            QgsWkbTypes.MultiPolygon, lotes_src.sourceCrs())
        if sink is None:
            raise QgsProcessingException(
                "Nenhuma camada de quadras escolhida e a saída 'Quadras' está desativada.")

        feedback.pushInfo("Nenhuma camada de quadras escolhida: criando camada nova.")
        criadas = 0
        for ch, (orig2, orig1), geom in novas:
            geom = QgsGeometry(geom)
            geom.convertToMultiType()
            f = QgsFeature(campos)
            f.setGeometry(geom)
            f.setAttributes([str(uuid.uuid4())] + ([orig2] if campo_lote_2 else [])
                            + [orig1, round(geom.area(), 3)])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            criadas += 1

        feedback.pushInfo(f"Quadras criadas: {criadas}")
        return {self.SAIDA: dest_id, "ATUALIZADAS": 0, "CRIADAS": criadas, "IGNORADAS": 0}

    @staticmethod
    def _valor_para_campo(layer, idx, valor):
        if valor is None:
            return None
        campo = layer.fields().at(idx)
        if campo.isNumeric():
            try:
                f = float(str(valor).strip().replace(",", "."))
                return int(f) if f.is_integer() and campo.type() != QVariant.Double else f
            except ValueError:
                return None
        return str(valor).strip()
