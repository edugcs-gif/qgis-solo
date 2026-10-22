# -*- coding: utf-8 -*-
"""
Desconto de Beiral - plugin QGIS (equivalente ao 4BEIRAL do AutoCAD)

Uso:
  1. Deixe ativa a camada das edificacoes (ex.: "1 pav") e selecione o(s) poligono(s).
  2. Clique no botao "Desconto de Beiral".
  3. O recuo de 0.6 e aplicado nas faces livres; faces coladas na divisa
     (bordas da camada "Lotes") ficam intactas.
  4. A faixa removida (o beiral) e adicionada na camada "<nome da camada> beiral"
     (ex.: "1 pav beiral").

Tudo fica apenas no buffer de edicao. NADA e salvo: revise e clique em
"Salvar edicoes" nas duas camadas (ou desfaca com Ctrl+Z / Descartar edicoes).
"""

import math
import os

from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsWkbTypes, QgsGeometry, QgsPoint,
    QgsGeometryCollection, QgsLineString, QgsPolygon, QgsMultiPolygon,
    QgsCoordinateTransform, QgsFeatureRequest, QgsVectorDataProvider,
    QgsVectorLayerUtils, QgsFields, QgsPointXY, QgsVertexId, QgsExpression,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox
try:
    from qgis.PyQt.QtWidgets import QAction          # Qt5
except ImportError:
    from qgis.PyQt.QtGui import QAction              # Qt6

from .beiral_core import ErroBeiral
from . import historico

# ------------------------------------------------------------------
# CONFIGURACAO
# ------------------------------------------------------------------
DISTANCIA = 0.6              # recuo do beiral (unidade do SRC - metros)
TOLERANCIA_DIVISA = 0.05     # face a menos disso do limite do lote = divisa
DECIDIR_POR_LADO = True      # cada parede e divisa inteira ou livre inteira (pela maior parte dela)
COLINEAR_TUDO_LIVRE = True   # lados alinhados (vertice colinear): se um tem recuo, todos tem
ANGULO_COLINEAR = 5          # ate quantos graus de virada dois lados contam como a mesma parede
COLADO_MAX = 0.01            # lado a no maximo isso da divisa/vizinha e 'colado de verdade' (nao entra)
FORCAR_POLIGONO_UNICO = True # ao restaurar, junta as partes que so se encostam (fica poligono simples)
FOLGA_JUNTAR_PARTES = 0.05   # vao maximo entre partes que a restauracao fecha (m)
NOME_CAMADA_LOTES = "Lotes"  # nome da camada usada como base das divisas
# Alguns "lotes" foram desenhados com o mesmo contorno da edificacao. Esses nao
# sao divisa de verdade e sao ignorados no calculo (senao nada seria descontado).
# Parede geminada: onde outra edificacao esta encostada, nao existe beiral,
# entao essa face tambem fica sem recuo (mesmo longe da linha do lote).
SEM_RECUO_ENTRE_GEMINADAS = True
FOLGA_GEMINADA = 0.2         # distancia maxima ate a edificacao vizinha (m)
IGNORAR_LOTE_IGUAL = True
FOLGA_LOTE_IGUAL = 0.2       # quanto o contorno do lote pode diferir e ainda ser "igual" (m)

CRIAR_BEIRAL = True          # adiciona o poligono do beiral na camada de beiral
SUFIXO_CAMADA_BEIRAL = " beiral"   # "1 pav" -> "1 pav beiral"
# Campo que liga o beiral a sua edificacao (o beiral recebe o mesmo valor).
# E por ele que o botao "Restaurar original" encontra o beiral certo.
CAMPO_VINCULO = "uuid"
COPIAR_ATRIBUTOS = True      # copia campos de mesmo nome da edificacao p/ o beiral
PASSO_ANALISE = 0.5          # as faces sao analisadas em trechos deste tamanho (m)
# Onde a parede livre encontra a divisa em angulo, a faixa e esticada ate a linha
# do lote, para nao sobrar aquela pontinha de edificacao no canto.
ESTICAR_NA_DIVISA = True
ANGULO_MIN_ESTICAR = 30      # so estica se a divisa virar pelo menos isso (graus)
EXTENSAO_NA_DIVISA = 2.0     # quanto a faixa pode ser esticada (m); o corte extra
                             # fica sempre dentro da faixa de DISTANCIA da divisa
TOLERANCIA_SIMPLIFICACAO = 1e-6    # so tira vertice colinear criado pelo calculo (nao desloca nada)
LIMITE_CANTO = 5.0           # cantos muito agudos sao cortados em vez de virar bico comprido
# campos que nunca sao copiados da edificacao para o beiral
CAMPOS_NAO_COPIAR = ("fid", "id", "ogc_fid", "gid", "area", "area_m2", "shape_area")
AREA_MIN_PARTE = 0.25        # pedacos menores que isso (m2) viram beiral em vez de sobrar
AREA_MIN_BEIRAL = 0.25       # pedaço de beiral menor que isso nao vira feicao (a area fica com a edificacao)
LARGURA_MIN_BEIRAL = 0.2     # a ponta do beiral e cortada onde ela fica mais fina que isso
LARGURA_ESPINHO = 0.02       # espinho (contorno que volta sobre si mesmo) mais fino que isso e apagado
AREA_MAX_ESPINHO = 0.3       # quanto de area a limpeza dos espinhos pode mexer (m2)
LARGURA_FIO = 0.005          # fio de largura quase nula (ex.: no lado da divisa) e desfeito
COMPRIMENTO_MIN_DIVISA = 0.5 # trecho colado na divisa menor que isso nao interrompe o recuo
TITULO = "Desconto de Beiral"
ATALHO = "Ctrl+Alt+B"
ATALHO_RESTAURAR = "Ctrl+Alt+Shift+B"   # botao "Restaurar original"
TITULO_RESTAURAR = "Restaurar original (desfazer beiral)"        # atalho padrao (pode ser trocado em Configuracoes > Atalhos de teclado)


def _enum(*caminhos):
    """Primeiro enum existente (compatibilidade QGIS 3.x / 4.x)."""
    for obj, nomes in caminhos:
        try:
            v = obj
            for n in nomes.split("."):
                v = getattr(v, n)
            return v
        except AttributeError:
            continue
    raise AttributeError(caminhos)


NIVEL_AVISO = _enum((Qgis, "MessageLevel.Warning"), (Qgis, "Warning"))
NIVEL_CRITICO = _enum((Qgis, "MessageLevel.Critical"), (Qgis, "Critical"))
NIVEL_SUCESSO = _enum((Qgis, "MessageLevel.Success"), (Qgis, "Success"))
CAP_ALTERAR_GEOM = _enum((Qgis, "VectorProviderCapability.ChangeGeometries"),
                         (QgsVectorDataProvider, "ChangeGeometries"))
CAP_ADICIONAR = _enum((Qgis, "VectorProviderCapability.AddFeatures"),
                      (QgsVectorDataProvider, "AddFeatures"))
ORIGENS_OK = (_enum((Qgis, "FieldOrigin.Provider"), (QgsFields, "OriginProvider")),
              _enum((Qgis, "FieldOrigin.Edit"), (QgsFields, "OriginEdit")))
DIR_REVERSA = _enum((Qgis, "TransformDirection.Reverse"),
                    (QgsCoordinateTransform, "ReverseTransform"))
BTN_SIM = _enum((QMessageBox, "StandardButton.Yes"), (QMessageBox, "Yes"))
BTN_NAO = _enum((QMessageBox, "StandardButton.No"), (QMessageBox, "No"))
PONTA_RETA = _enum((Qgis, "EndCapStyle.Flat"), (QgsGeometry, "CapFlat"))
CANTO_VIVO = _enum((Qgis, "JoinStyle.Miter"), (QgsGeometry, "JoinStyleMiter"))


def _tipo_poligono():
    try:
        return Qgis.GeometryType.Polygon          # QGIS >= 3.30
    except AttributeError:
        return QgsWkbTypes.PolygonGeometry


def _eh_poligono(camada):
    return (isinstance(camada, QgsVectorLayer)
            and camada.geometryType() == _tipo_poligono())


def _vazio(valor):
    return valor is None or (hasattr(valor, "isNull") and valor.isNull()) or str(valor).strip() in ("", "NULL")


def _achar_camada(nome, ignorar_id=None):
    """Procura camada de poligono pelo nome (exato, depois sem maiusc./espacos extras)."""
    proj = QgsProject.instance()
    camadas = [c for c in proj.mapLayersByName(nome) if _eh_poligono(c)]
    if not camadas:
        alvo = " ".join(nome.split()).lower()
        camadas = [c for c in proj.mapLayers().values()
                   if _eh_poligono(c) and " ".join(c.name().split()).lower() == alvo]
    camadas = [c for c in camadas if c.id() != ignorar_id]
    return camadas[0] if camadas else None


def _reverse(transf, caixa):
    return transf.transformBoundingBox(caixa, DIR_REVERSA)


def _ajustar_tipo(geom, tipo_camada):
    """Compatibiliza Z/M/multi com o tipo da camada (geom e alterada no lugar)."""
    if QgsWkbTypes.isMultiType(tipo_camada) and not geom.isMultipart():
        geom.convertToMultiType()
    elif not QgsWkbTypes.isMultiType(tipo_camada) and geom.isMultipart():
        if not geom.convertToSingleType():
            raise ErroBeiral("camada e de poligono simples, mas a geometria tem varias partes")
    if QgsWkbTypes.hasZ(tipo_camada) and not QgsWkbTypes.hasZ(geom.wkbType()):
        geom.get().addZValue(0)
    elif not QgsWkbTypes.hasZ(tipo_camada) and QgsWkbTypes.hasZ(geom.wkbType()):
        geom.get().dropZValue()
    if QgsWkbTypes.hasM(tipo_camada) and not QgsWkbTypes.hasM(geom.wkbType()):
        geom.get().addMValue(0)
    elif not QgsWkbTypes.hasM(tipo_camada) and QgsWkbTypes.hasM(geom.wkbType()):
        geom.get().dropMValue()
    return geom


class DescontoBeiralPlugin:

    def __init__(self, iface, toolbar=None, menu=None):
        self.iface = iface
        self.toolbar = toolbar                 # barra única do Ferramentas CAD
        self.menu = menu or ("&" + TITULO)
        self.action = None
        self.action_rest = None

    # ------------------------------------------------------------------
    def initGui(self):
        icone = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icone, TITULO + " (%.2f)" % DISTANCIA,
                              self.iface.mainWindow())
        self.action.setToolTip(
            "Recuo de %.2f nas faces livres da edificacao selecionada,\n"
            "mantendo as faces coladas na divisa (camada '%s'),\n"
            "e cria o poligono do beiral na camada '<camada>%s'.\n"
            "Nao salva: as alteracoes ficam pendentes na edicao."
            % (DISTANCIA, NOME_CAMADA_LOTES, SUFIXO_CAMADA_BEIRAL))
        self.action.setObjectName("mDescontoBeiral")
        self.action.triggered.connect(self.run)
        # registra no gerenciador de atalhos do QGIS (aparece em Configuracoes > Atalhos de teclado)
        self.iface.registerMainWindowAction(self.action, ATALHO)
        self._barra_add(self.action)
        self.iface.addPluginToVectorMenu(self.menu, self.action)

        icone2 = QIcon(os.path.join(os.path.dirname(__file__), "icon_restaurar.png"))
        self.action_rest = QAction(icone2, TITULO_RESTAURAR, self.iface.mainWindow())
        self.action_rest.setToolTip(
            "Volta a edificacao selecionada para a geometria original (antes do\n"
            "desconto) e apaga o beiral dela na camada '<camada>%s'.\n"
            "Nao salva: as alteracoes ficam pendentes na edicao." % SUFIXO_CAMADA_BEIRAL)
        self.action_rest.setObjectName("mRestaurarBeiral")
        self.action_rest.triggered.connect(self.restaurar)
        self.iface.registerMainWindowAction(self.action_rest, ATALHO_RESTAURAR)
        self._barra_add(self.action_rest)
        self.iface.addPluginToVectorMenu(self.menu, self.action_rest)

    def _barra_add(self, acao):
        if self.toolbar is not None:
            self.toolbar.addAction(acao)
        else:
            self.iface.addToolBarIcon(acao)

    def _barra_rem(self, acao):
        if self.toolbar is not None:
            try:
                self.toolbar.removeAction(acao)
            except RuntimeError:
                pass
        else:
            self.iface.removeToolBarIcon(acao)

    def unload(self):
        if getattr(self, "action_rest", None):
            self.iface.unregisterMainWindowAction(self.action_rest)
            self._barra_rem(self.action_rest)
            self.iface.removePluginVectorMenu(self.menu, self.action_rest)
            self.action_rest.deleteLater()
            self.action_rest = None
        if self.action:
            self.iface.unregisterMainWindowAction(self.action)
            self._barra_rem(self.action)
            self.iface.removePluginVectorMenu(self.menu, self.action)
            self.action.deleteLater()
            self.action = None

    # ------------------------------------------------------------------
    def _msg(self, texto, nivel=NIVEL_AVISO):
        self.iface.messageBar().pushMessage(TITULO, texto, level=nivel, duration=8)

    def run(self):
        try:
            self._executar()
        except Exception as e:  # nunca deixa excecao solta no QGIS
            import traceback
            QMessageBox.critical(self.iface.mainWindow(), TITULO,
                                 "Erro inesperado - nada foi alterado.\n\n%s\n\n%s"
                                 % (e, traceback.format_exc()))

    # ------------------------------------------------------------------
    def _executar(self):
        camada = self.iface.activeLayer()

        # ---------- validacoes ----------
        if not _eh_poligono(camada):
            self._msg("Selecione (ative) a camada de edificacoes (poligonos).")
            return
        if camada.name().strip().lower().endswith(SUFIXO_CAMADA_BEIRAL.strip().lower()):
            self._msg("A camada ativa e uma camada de beiral. Ative a camada da edificacao.")
            return
        lotes = _achar_camada(NOME_CAMADA_LOTES)
        if lotes is None:
            self._msg("Camada '%s' nao encontrada no projeto." % NOME_CAMADA_LOTES, NIVEL_CRITICO)
            return
        if lotes.id() == camada.id():
            self._msg("A camada ativa e a propria '%s'. Ative a camada de edificacoes."
                      % NOME_CAMADA_LOTES)
            return
        if camada.selectedFeatureCount() == 0:
            self._msg("Nenhum poligono selecionado na camada '%s'." % camada.name())
            return
        if camada.crs().isGeographic():
            self._msg("A camada esta em SRC geografico (graus). Use um SRC projetado (ex.: UTM).",
                      NIVEL_CRITICO)
            return
        if not camada.dataProvider().capabilities() & CAP_ALTERAR_GEOM:
            self._msg("A camada '%s' nao permite alterar geometrias." % camada.name(), NIVEL_CRITICO)
            return

        # ---------- camada de beiral ----------
        beiral = None
        if CRIAR_BEIRAL:
            nome_beiral = camada.name().strip() + SUFIXO_CAMADA_BEIRAL
            beiral = _achar_camada(nome_beiral, ignorar_id=camada.id())
            if beiral is None:
                resp = QMessageBox.question(
                    self.iface.mainWindow(), TITULO,
                    "Camada '%s' nao encontrada no projeto.\n\n"
                    "Deseja continuar SOMENTE com o desconto (sem criar o beiral)?" % nome_beiral,
                    BTN_SIM | BTN_NAO, BTN_NAO)
                if resp != BTN_SIM:
                    return
            elif not beiral.dataProvider().capabilities() & CAP_ADICIONAR:
                self._msg("A camada '%s' nao permite adicionar feicoes." % beiral.name(),
                          NIVEL_CRITICO)
                return

        t_lotes = None
        if lotes.crs() != camada.crs():
            t_lotes = QgsCoordinateTransform(lotes.crs(), camada.crs(), QgsProject.instance())
        t_beiral = None
        mapa_campos = []
        if beiral is not None:
            if beiral.crs() != camada.crs():
                t_beiral = QgsCoordinateTransform(camada.crs(), beiral.crs(), QgsProject.instance())
            if COPIAR_ATRIBUTOS:
                mapa_campos = self._mapa_campos(camada, beiral)

        vinc_e = camada.fields().indexOf(CAMPO_VINCULO)
        vinc_b = beiral.fields().indexOf(CAMPO_VINCULO) if beiral is not None else -1
        if beiral is not None and (vinc_e < 0 or vinc_b < 0):
            self._msg("Campo '%s' nao existe nas duas camadas: o beiral sera criado sem "
                      "vinculo com a edificacao." % CAMPO_VINCULO)

        # ---------- 1. calcula tudo ANTES de editar ----------
        novas = {}          # fid -> nova geometria da edificacao
        originais = {}      # fid -> geometria original (para o botao Restaurar)
        beirais = []        # (geometria, atributos) a adicionar
        erros, avisos = [], []
        for feat in camada.getSelectedFeatures():
            fid = feat.id()
            try:
                orig, nova, aviso = self._descontar_feicao(feat, camada, lotes, t_lotes)
                partes = []
                if beiral is not None:
                    partes = self._gerar_beiral(orig, nova, beiral, t_beiral)
                    if not partes:
                        raise ErroBeiral("nao foi possivel gerar o poligono do beiral")
                attrs = {ib: feat.attribute(ie) for ib, ie in mapa_campos}
                if vinc_b >= 0 and vinc_e >= 0:
                    valor = feat.attribute(vinc_e)
                    if _vazio(valor):
                        avisos.append("ID %s: edificacao sem %s - beiral criado sem vinculo"
                                      % (fid, CAMPO_VINCULO))
                    else:
                        attrs[vinc_b] = valor
                novas[fid] = nova
                originais[fid] = QgsGeometry(feat.geometry())
                beirais.extend((g, attrs) for g in partes)
                if aviso:
                    avisos.append("ID %s: %s" % (fid, aviso))
            except ErroBeiral as e:
                erros.append((fid, str(e)))

        if not novas:
            self._relatorio(camada, beiral, 0, 0, erros, avisos, editou=False)
            return

        # ---------- 2. aplica no buffer de edicao (SEM salvar) ----------
        camadas_edit = [camada] + ([beiral] if beiral is not None else [])
        for c in camadas_edit:
            if not c.isEditable() and not c.startEditing():
                self._msg("Nao foi possivel colocar a camada '%s' em edicao." % c.name(),
                          NIVEL_CRITICO)
                return

        abertos = []
        try:
            if beiral is not None:
                beiral.beginEditCommand("%s - beiral (%d)" % (TITULO, len(beirais)))
                abertos.append(beiral)
                novas_feats = []
                for g, a in beirais:
                    nf = QgsVectorLayerUtils.createFeature(beiral, g, a)
                    if vinc_b >= 0 and vinc_b in a:
                        nf.setAttribute(vinc_b, a[vinc_b])   # garante o vinculo (uuid)
                    novas_feats.append(nf)
                if not beiral.addFeatures(novas_feats):
                    raise RuntimeError("falha ao adicionar os poligonos na camada '%s'"
                                       % beiral.name())

            camada.beginEditCommand("%s %.2f (%d feicoes)" % (TITULO, DISTANCIA, len(novas)))
            abertos.append(camada)
            falhas = [fid for fid, g in novas.items() if not camada.changeGeometry(fid, g)]
            if falhas:
                raise RuntimeError("changeGeometry falhou para: %s" % falhas)

            for c in abertos:
                c.endEditCommand()
        except Exception:
            for c in abertos:
                c.destroyEditCommand()   # desfaz tudo que foi feito nesta execucao
            raise

        try:
            historico.salvar(historico.chave_camada(camada),
                             [(fid, originais[fid].asWkt(17), g.asWkt(17))
                              for fid, g in novas.items()])
        except Exception as e:      # o historico e so uma ajuda, nunca impede o desconto
            avisos.append("nao foi possivel gravar o historico para restaurar: %s" % e)

        for c in camadas_edit:
            c.triggerRepaint()
        self._relatorio(camada, beiral, len(novas), len(beirais), erros, avisos, editou=True)

    # ------------------------------------------------------------------
    # RESTAURAR ORIGINAL
    # ------------------------------------------------------------------
    def restaurar(self):
        try:
            self._restaurar()
        except Exception as e:
            import traceback
            QMessageBox.critical(self.iface.mainWindow(), TITULO_RESTAURAR,
                                 "Erro inesperado - nada foi alterado.\n\n%s\n\n%s"
                                 % (e, traceback.format_exc()))

    def _restaurar(self):
        camada = self.iface.activeLayer()
        if not _eh_poligono(camada):
            self._msg("Ative a camada de edificacoes e selecione o(s) poligono(s).")
            return
        if camada.name().strip().lower().endswith(SUFIXO_CAMADA_BEIRAL.strip().lower()):
            self._msg("A camada ativa e a de beiral. Ative a camada da edificacao.")
            return
        if camada.selectedFeatureCount() == 0:
            self._msg("Nenhum poligono selecionado na camada '%s'." % camada.name())
            return
        if not camada.dataProvider().capabilities() & CAP_ALTERAR_GEOM:
            self._msg("A camada '%s' nao permite alterar geometrias." % camada.name(),
                      NIVEL_CRITICO)
            return

        nome_beiral = camada.name().strip() + SUFIXO_CAMADA_BEIRAL
        beiral = _achar_camada(nome_beiral, ignorar_id=camada.id())
        if beiral is None:
            self._msg("Camada '%s' nao encontrada." % nome_beiral, NIVEL_CRITICO)
            return
        t_beiral = None
        if beiral.crs() != camada.crs():
            t_beiral = QgsCoordinateTransform(camada.crs(), beiral.crs(), QgsProject.instance())

        vinc_e = camada.fields().indexOf(CAMPO_VINCULO)
        vinc_b = beiral.fields().indexOf(CAMPO_VINCULO)
        usar_vinculo = vinc_e >= 0 and vinc_b >= 0
        chave = historico.chave_camada(camada)

        # agrupa a selecao pelo uuid (unidades com o mesmo uuid sao a mesma edificacao)
        grupos = {}
        for feat in camada.getSelectedFeatures():
            valor = feat.attribute(vinc_e) if usar_vinculo else None
            if _vazio(valor):
                grupos[("fid", feat.id())] = [feat.id()]
                continue
            if valor in grupos:
                continue
            expr = '"%s" = %s' % (CAMPO_VINCULO, QgsExpression.quotedValue(valor))
            grupos[valor] = [f.id() for f in camada.getFeatures(
                QgsFeatureRequest().setFilterExpression(expr).setNoAttributes())]

        restaurar, apagar = {}, set()
        erros, avisos = [], []
        for chave_grupo, fids in grupos.items():
            rotulo = ("ID %s" % chave_grupo[1]) if isinstance(chave_grupo, tuple) \
                else ("%s %s" % (CAMPO_VINCULO, chave_grupo))
            try:
                atuais = {fid: QgsGeometry(camada.getFeature(fid).geometry()) for fid in fids}

                # 1) beirais desta edificacao
                if isinstance(chave_grupo, tuple):
                    avisos.append("%s: sem %s - beiral procurado pela posicao"
                                  % (rotulo, CAMPO_VINCULO))
                    beirais = None
                else:
                    expr = '"%s" = %s' % (CAMPO_VINCULO, QgsExpression.quotedValue(chave_grupo))
                    beirais = {}
                    for bf in beiral.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
                        g = QgsGeometry(bf.geometry())
                        if g.isNull() or g.isEmpty():
                            continue
                        if t_beiral is not None:
                            g.transform(t_beiral, DIR_REVERSA)
                        beirais[bf.id()] = g

                # 2) geometria original: pelo historico (exato) ...
                orig = None
                for fid, g in atuais.items():
                    try:
                        reg = historico.buscar(chave, fid)
                    except Exception:
                        reg = None
                    if reg is None:
                        continue
                    g_desc = QgsGeometry.fromWkt(reg[1])
                    if (not g_desc.isEmpty() and abs(g_desc.area() - g.area()) < 1e-4
                            and g_desc.hausdorffDistance(g) < 0.001):
                        orig = QgsGeometry.fromWkt(reg[0])
                        break

                base = min(atuais.values(), key=lambda x: x.area())   # a descontada e a menor
                if beirais is None:
                    # sem uuid: procura pela posicao (como antes)
                    if orig is not None:
                        ids = self._beirais_da_faixa(beiral, orig.difference(base), t_beiral)
                    else:
                        ids, orig = self._reconstruir_pela_juncao(base, beiral, t_beiral)
                        if orig is None:
                            raise ErroBeiral("nenhum beiral encostado encontrado")
                else:
                    if not beirais and orig is None:
                        raise ErroBeiral("nenhum beiral com este %s - nada a restaurar"
                                         % CAMPO_VINCULO)
                    # so aceita beiral que fica junto da edificacao (protege contra erro de dado)
                    perto = (orig if orig is not None else base).buffer(1.0, 8)
                    ids = set(i for i, g in beirais.items() if perto.contains(g))
                    longe = set(beirais) - ids
                    if longe:
                        avisos.append("%s: %d beiral(is) com este %s estao longe da edificacao "
                                      "e NAO foram apagados (fids %s)"
                                      % (rotulo, len(longe), CAMPO_VINCULO, sorted(longe)))
                    if orig is None:
                        if not ids:
                            raise ErroBeiral("os beirais com este %s nao encostam na edificacao"
                                             % CAMPO_VINCULO)
                        orig = QgsGeometry.unaryUnion([base] + [beirais[i] for i in ids])
                        orig = self._tirar_espinhos(orig)
                        orig = self._tirar_vertices_extras(orig) or orig
                        orig = self._tirar_espinhos(orig)
                        if FORCAR_POLIGONO_UNICO:
                            antes = len([p for p in orig.asGeometryCollection()
                                         if p.type() == _tipo_poligono()])
                            if antes > 1:
                                orig = self._juntar_partes(orig)
                                depois = len([p for p in orig.asGeometryCollection()
                                              if p.type() == _tipo_poligono()])
                                if depois == 1:
                                    avisos.append("%s: as partes so se encostavam e foram "
                                                  "soldadas num poligono simples" % rotulo)
                                else:
                                    avisos.append("%s: restaurado com %d partes separadas "
                                                  "(vao maior que %.2f m)"
                                                  % (rotulo, depois, FOLGA_JUNTAR_PARTES))
                        avisos.append("%s: restaurado juntando edificacao + beiral "
                                      "(sem registro exato do desconto)" % rotulo)

                if orig is None or orig.isEmpty() or not orig.isGeosValid():
                    raise ErroBeiral("a geometria restaurada ficou invalida")
                mudou = 0
                for fid, g in atuais.items():
                    nova = QgsGeometry(orig)
                    _ajustar_tipo(nova, camada.wkbType())
                    if g.hausdorffDistance(nova) > 1e-6 or abs(g.area() - nova.area()) > 1e-6:
                        restaurar[fid] = nova
                        mudou += 1
                if len(fids) > 1:
                    avisos.append("%s: %d unidades com o mesmo %s restauradas juntas"
                                  % (rotulo, len(fids), CAMPO_VINCULO))
                if mudou == 0 and not ids:
                    raise ErroBeiral("ja esta na geometria original")
                apagar.update(ids)
            except ErroBeiral as e:
                erros.append((rotulo, str(e)))

        if not restaurar and not apagar:
            texto = "Nenhuma edificacao foi restaurada."
            if erros:
                texto += "\n\n" + "\n".join("  - %s: %s" % x for x in erros[:30])
            QMessageBox.warning(self.iface.mainWindow(), TITULO_RESTAURAR, texto)
            return

        resumo = ("Restaurar %d edificacao(oes) para a geometria original e apagar %d "
                  "poligono(s) de beiral na camada '%s'?\n\n(nada sera salvo)"
                  % (len(restaurar), len(apagar), beiral.name()))
        if erros:
            resumo += "\n\nNao serao restauradas (%d):\n" % len(erros)
            resumo += "\n".join("  - %s: %s" % x for x in erros[:15])
        if QMessageBox.question(self.iface.mainWindow(), TITULO_RESTAURAR, resumo,
                                BTN_SIM | BTN_NAO, BTN_NAO) != BTN_SIM:
            return

        camadas_edit = ([camada] if restaurar else []) + ([beiral] if apagar else [])
        for c in camadas_edit:
            if not c.isEditable() and not c.startEditing():
                self._msg("Nao foi possivel colocar a camada '%s' em edicao." % c.name(),
                          NIVEL_CRITICO)
                return

        abertos = []
        try:
            if restaurar:
                camada.beginEditCommand("%s (%d)" % (TITULO_RESTAURAR, len(restaurar)))
                abertos.append(camada)
                falhas = [fid for fid, g in restaurar.items() if not camada.changeGeometry(fid, g)]
                if falhas:
                    raise RuntimeError("changeGeometry falhou para: %s" % falhas)
            if apagar:
                beiral.beginEditCommand("%s - apaga beiral (%d)" % (TITULO_RESTAURAR, len(apagar)))
                abertos.append(beiral)
                if not beiral.deleteFeatures(list(apagar)):
                    raise RuntimeError("falha ao apagar os beirais")
            for c in abertos:
                c.endEditCommand()
        except Exception:
            for c in abertos:
                c.destroyEditCommand()
            raise

        for c in camadas_edit:
            c.triggerRepaint()
        msg = ("%d edificacao(oes) restaurada(s), %d beiral(is) apagado(s). "
               "NAO SALVO - confira e salve manualmente." % (len(restaurar), len(apagar)))
        if avisos or erros:
            linhas = [msg]
            if avisos:
                linhas += ["", "Avisos:"] + ["  - " + a for a in avisos[:30]]
            if erros:
                linhas += ["", "Nao restauradas (%d):" % len(erros)]
                linhas += ["  - %s: %s" % x for x in erros[:30]]
            QMessageBox.warning(self.iface.mainWindow(), TITULO_RESTAURAR, "\n".join(linhas))
        else:
            self.iface.messageBar().pushMessage(TITULO_RESTAURAR, msg,
                                                level=NIVEL_SUCESSO, duration=10)

    @staticmethod
    def _juntar_partes(geom):
        """Ao juntar edificacao + beiral as vezes sobra um fiozinho de vao e o
        resultado vira multipoligono. Aqui as partes que so se encostam sao
        soldadas, devolvendo um poligono simples."""
        if geom is None or geom.isEmpty():
            return geom
        partes = [p for p in geom.asGeometryCollection() if p.type() == _tipo_poligono()]
        if len(partes) <= 1:
            return geom
        for eps in (0.002, 0.01, FOLGA_JUNTAR_PARTES / 2.0):
            fechado = geom.buffer(eps, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO)
            if fechado is None or fechado.isEmpty():
                continue
            fechado = fechado.buffer(-eps, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO)
            if fechado is None or fechado.isEmpty() or not fechado.isGeosValid():
                continue
            novas = [p for p in fechado.asGeometryCollection() if p.type() == _tipo_poligono()]
            if len(novas) == 1 and abs(fechado.area() - geom.area()) < 0.05:
                fechado = DescontoBeiralPlugin._tirar_espinhos(fechado)
                fechado = DescontoBeiralPlugin._tirar_vertices_extras(fechado) or fechado
                return fechado
        return geom

    @staticmethod
    def _beirais_da_faixa(beiral, faixa, transf):
        """fids de beiral que estao (quase) inteiros dentro da faixa descontada."""
        if beiral is None or faixa is None or faixa.isEmpty():
            return set()
        caixa = faixa.boundingBox()
        caixa.grow(0.01)
        if transf is not None:
            caixa = transf.transformBoundingBox(caixa)
        ids = set()
        for f in beiral.getFeatures(QgsFeatureRequest().setFilterRect(caixa).setNoAttributes()):
            g = QgsGeometry(f.geometry())
            if g.isNull() or g.isEmpty() or g.area() <= 0:
                continue
            if transf is not None:
                g.transform(transf, DIR_REVERSA)
            dentro = g.intersection(faixa)
            if dentro is not None and not dentro.isEmpty() and dentro.area() >= 0.9 * g.area():
                ids.add(f.id())
        return ids

    @staticmethod
    def _reconstruir_pela_juncao(atual, beiral, transf):
        """Sem registro: junta a edificacao com os beirais encostados nela.
        So aceita beiral que encosta ao longo de uma linha, nao sobrepoe a
        edificacao e fica todo a menos de 1 m dela (nao pega beiral do vizinho)."""
        caixa = atual.boundingBox()
        caixa.grow(1.0)
        if transf is not None:
            caixa = transf.transformBoundingBox(caixa)
        perto = atual.buffer(1.0, 8)
        contorno = QgsGeometry(atual.constGet().boundary())
        ids, pecas = set(), [atual]
        for f in beiral.getFeatures(QgsFeatureRequest().setFilterRect(caixa).setNoAttributes()):
            g = QgsGeometry(f.geometry())
            if g.isNull() or g.isEmpty():
                continue
            if transf is not None:
                g.transform(transf, DIR_REVERSA)
            if g.distance(atual) > 0.001:
                continue
            sobre = g.intersection(atual)
            if sobre is not None and not sobre.isEmpty() and sobre.area() > 0.01:
                continue                       # sobrepoe: nao e o beiral desta
            if not perto.contains(g):
                continue                       # vai longe demais: e de outra edificacao
            encosto = contorno.intersection(g.buffer(0.001, 4))
            if encosto is None or encosto.isEmpty() or encosto.length() < 0.1:
                continue                       # so encosta num ponto
            ids.add(f.id())
            pecas.append(g)
        if not ids:
            return set(), None
        junta = QgsGeometry.unaryUnion(pecas)
        if junta is None or junta.isEmpty():
            return set(), None
        junta = DescontoBeiralPlugin._tirar_espinhos(junta)
        junta = DescontoBeiralPlugin._tirar_vertices_extras(junta) or junta
        junta = DescontoBeiralPlugin._tirar_espinhos(junta)
        if FORCAR_POLIGONO_UNICO:
            junta = DescontoBeiralPlugin._juntar_partes(junta)
        return ids, junta

    # ------------------------------------------------------------------
    @staticmethod
    def _mapa_campos(origem, destino):
        """Pares (idx_destino, idx_origem) de campos com o mesmo nome que podem ser copiados.
        Ignora chave primaria, campos com valor padrao (ex.: area, uuid, sequencia)
        e campos virtuais/de juncao."""
        pk = set(destino.primaryKeyAttributes())
        prov = destino.dataProvider()
        pares = []
        campos = destino.fields()
        for idx in range(campos.count()):
            if idx in pk:
                continue
            if campos.fieldOrigin(idx) not in ORIGENS_OK:
                continue
            if campos.at(idx).defaultValueDefinition().isValid():
                continue
            if campos.fieldOrigin(idx) == ORIGENS_OK[0]:
                pidx = campos.fieldOriginIndex(idx)
                if prov.defaultValueClause(pidx):
                    continue
            nome = campos.at(idx).name()
            if nome.lower() in CAMPOS_NAO_COPIAR:
                continue
            ie = origem.fields().indexOf(nome)
            if ie >= 0:
                pares.append((idx, ie))
        return pares

    def _gerar_beiral(self, orig, nova, beiral, transf):
        """Beiral = edificacao original - edificacao descontada."""
        dif = orig.difference(nova)
        if dif is None or dif.isEmpty():
            return []
        partes = []
        for p in dif.asGeometryCollection():
            if p.type() != _tipo_poligono() or p.area() < AREA_MIN_BEIRAL:
                continue
            aberto = self._tirar_fios(p)
            for q in aberto.asGeometryCollection():
                if q.type() != _tipo_poligono() or q.area() < AREA_MIN_BEIRAL:
                    continue
                limpo = self._tirar_vertices_extras(q, orig, nova) or q
                partes.append(self._tirar_espinhos(limpo))
        esperado = sum(p.area() for p in dif.asGeometryCollection()
                       if p.type() == _tipo_poligono() and p.area() >= AREA_MIN_BEIRAL)
        obtido = sum(p.area() for p in partes)
        if abs(obtido - esperado) > max(0.05, esperado * 0.01):
            raise ErroBeiral("area do beiral inconsistente (esperado %.3f, obtido %.3f)"
                             % (esperado, obtido))
        for p in partes:
            if not p.isGeosValid():
                raise ErroBeiral("poligono do beiral ficou invalido")

        if QgsWkbTypes.isMultiType(beiral.wkbType()):
            geoms = [QgsGeometry.collectGeometry(partes)]
        else:
            geoms = partes          # camada simples: uma feicao por faixa

        saida = []
        for g in geoms:
            g = QgsGeometry(g)
            if transf is not None:
                g.transform(transf)
            saida.append(_ajustar_tipo(g, beiral.wkbType()))
        return saida

    # ------------------------------------------------------------------
    def _descontar_feicao(self, feat, camada, lotes, transf):
        geom = QgsGeometry(feat.geometry())
        if geom.isNull() or geom.isEmpty():
            raise ErroBeiral("feicao sem geometria")

        aviso = None
        if QgsWkbTypes.isCurvedType(geom.wkbType()):
            geom = QgsGeometry(geom.constGet().segmentize())
            aviso = "possuia arcos; foram convertidos em segmentos retos"
        if not geom.isGeosValid():
            raise ErroBeiral("a geometria da edificacao e invalida (use Verificar validade)")

        divisa = self._divisas(geom, lotes, transf)
        motor = QgsGeometry.createGeometryEngine(divisa.constGet())
        motor.prepareGeometry()

        limites = divisa
        motor_gem = None
        if SEM_RECUO_ENTRE_GEMINADAS:
            vizinhas = self._geminadas(feat, camada, geom)
            if vizinhas is not None:
                motor_gem = QgsGeometry.createGeometryEngine(vizinhas.constGet())
                motor_gem.prepareGeometry()
                limites = QgsGeometry.collectGeometry([divisa, vizinhas])

        def na_divisa(p1, p2):
            # o trecho inteiro precisa estar sobre a divisa
            pontos = []
            for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                pontos.append(QgsPoint(p1[0] + (p2[0] - p1[0]) * t,
                                       p1[1] + (p2[1] - p1[1]) * t))
            if all(motor.distance(q) < TOLERANCIA_DIVISA for q in pontos):
                return True                     # face sobre a linha do lote
            if motor_gem is not None and all(motor_gem.distance(q) < FOLGA_GEMINADA
                                             for q in pontos):
                return True                     # parede geminada com a vizinha
            return False

        def colada(p1, p2):
            """Parede realmente encostada (na linha do lote ou na vizinha), em toda a extensao."""
            for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                q = QgsPoint(p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t)
                d = motor.distance(q)
                if motor_gem is not None:
                    d = min(d, motor_gem.distance(q))
                if d > COLADO_MAX:
                    return False
            return True

        # ---- faixas de recuo: uma faixa de largura DISTANCIA sobre cada trecho livre ----
        faixas, pontas = [], []
        for linha, rumo_ini, rumo_fim in self._linhas_livres(geom, na_divisa, colada):
            faixas.append(linha.buffer(DISTANCIA, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO))
            if ESTICAR_NA_DIVISA:
                pontas += self._esticar_nas_pontas(linha, rumo_ini, rumo_fim)

        if not faixas:
            raise ErroBeiral("todas as faces estao na divisa - nada a descontar")

        if pontas:
            # o pedaco esticado so vale perto da divisa, para nao entrar na edificacao
            perto_divisa = limites.buffer(DISTANCIA, 12)
            extra = QgsGeometry.unaryUnion(pontas).intersection(perto_divisa)
            if extra is not None and not extra.isEmpty():
                faixas.append(extra)

        faixa = QgsGeometry.unaryUnion(faixas)
        if faixa is None or faixa.isEmpty():
            raise ErroBeiral("nao foi possivel calcular a faixa do beiral")

        nova = geom.difference(faixa)
        if nova is None or nova.isEmpty():
            raise ErroBeiral("a edificacao e estreita demais para o recuo (sobrou nada)")
        nova = self._limpar(nova)
        if nova is None:
            raise ErroBeiral("a edificacao e estreita demais para o recuo (sobrou nada)")

        # ---- apara as agulhas do beiral (cantos fechados) e devolve a area a edificacao ----
        bruto = geom.difference(nova)
        aparado = self._aparar_beiral(bruto)
        if aparado is None or aparado.isEmpty():
            raise ErroBeiral("o beiral ficou so com lascas - nada a descontar")
        sobra = bruto.difference(aparado)
        pedacos = ([p for p in sobra.asGeometryCollection()
                    if p.type() == _tipo_poligono() and p.area() >= 1e-3]
                   if sobra is not None and not sobra.isEmpty() else [])
        if pedacos:
            juntas = QgsGeometry.unaryUnion([nova] + pedacos)
            if juntas is not None and not juntas.isEmpty() and juntas.isGeosValid():
                nova = self._limpar(juntas) or nova
        if nova is None:
            raise ErroBeiral("a edificacao e estreita demais para o recuo (sobrou nada)")
        nova = self._tirar_vertices_extras(nova, geom) or nova
        nova = self._tirar_espinhos(nova)

        _ajustar_tipo(nova, camada.wkbType())
        if QgsWkbTypes.hasZ(camada.wkbType()):
            self._repor_z(nova, geom)

        if not nova.isGeosValid():
            erro = nova.validateGeometry()
            detalhe = erro[0].what() if erro else "geometria invalida"
            raise ErroBeiral("resultado invalido (%s)" % detalhe)
        if nova.area() <= 0 or nova.area() >= geom.area():
            raise ErroBeiral("area resultante inconsistente (%.2f -> %.2f)"
                             % (geom.area(), nova.area()))
        if nova.constGet().partCount() > 1 and not QgsWkbTypes.isMultiType(camada.wkbType()):
            raise ErroBeiral("o recuo dividiu a edificacao em partes, mas a camada e de poligono simples")
        return geom, nova, aviso

    # ------------------------------------------------------------------
    @staticmethod
    def _geminadas(feat, camada, geom):
        """Contorno das edificacoes encostadas nesta (parede geminada). Ignora
        feicoes desenhadas por cima (outras unidades da mesma edificacao)."""
        caixa = geom.boundingBox()
        caixa.grow(FOLGA_GEMINADA + 0.01)
        req = QgsFeatureRequest().setFilterRect(caixa).setNoAttributes()
        bordas = []
        for outra in camada.getFeatures(req):
            if outra.id() == feat.id():
                continue
            g = outra.geometry()
            if g.isNull() or g.isEmpty():
                continue
            if g.distance(geom) >= FOLGA_GEMINADA:
                continue
            junto = g.intersection(geom)
            if junto is not None and not junto.isEmpty() and junto.area() > 0.05:
                continue               # desenhada por cima, nao e parede geminada
            b = g.constGet().boundary()
            if b is not None:
                bordas.append(QgsGeometry(b))
        if not bordas:
            return None
        return QgsGeometry.collectGeometry(bordas)

    # ------------------------------------------------------------------
    def _divisas(self, geom, lotes, transf):
        """Bordas dos lotes proximos da edificacao, no SRC da edificacao."""
        caixa = geom.boundingBox()
        caixa.grow(DISTANCIA + TOLERANCIA_DIVISA * 2 + 0.01)
        caixa_lotes = _reverse(transf, caixa) if transf is not None else caixa
        req = QgsFeatureRequest().setFilterRect(caixa_lotes).setNoAttributes()
        bordas = []
        for lote in lotes.getFeatures(req):
            g = QgsGeometry(lote.geometry())
            if g.isNull() or g.isEmpty():
                continue
            if transf is not None:
                g.transform(transf)
            if IGNORAR_LOTE_IGUAL and abs(g.area() - geom.area()) < 0.5 \
                    and g.hausdorffDistance(geom) < FOLGA_LOTE_IGUAL:
                continue               # lote desenhado por cima da propria edificacao
            b = g.constGet().boundary()
            if b is not None:
                bordas.append(QgsGeometry(b))
        if not bordas:
            raise ErroBeiral("nenhum lote da camada '%s' encontrado sob a edificacao"
                             % NOME_CAMADA_LOTES)
        return QgsGeometry.collectGeometry(bordas)

    @staticmethod
    def _linhas_livres(geom, na_divisa, colada=None):
        """Contorno (todos os aneis) dividido em trechos de ate PASSO_ANALISE metros;
        devolve as sequencias continuas de trechos que NAO estao na divisa."""
        abst = geom.constGet()
        partes = ([abst.geometryN(i) for i in range(abst.numGeometries())]
                  if isinstance(abst, QgsGeometryCollection) else [abst])
        linhas = []
        for parte in partes:
            aneis = [parte.exteriorRing()]
            aneis += [parte.interiorRing(k) for k in range(parte.numInteriorRings())]
            for anel in aneis:
                if anel is None:
                    continue
                # 1. quebra o anel em trechos curtos
                pontos, lado = [], []
                for i in range(anel.numPoints() - 1):
                    x1, y1 = anel.xAt(i), anel.yAt(i)
                    x2, y2 = anel.xAt(i + 1), anel.yAt(i + 1)
                    comp = math.hypot(x2 - x1, y2 - y1)
                    if comp < 1e-9:
                        continue
                    n = max(1, int(math.ceil(comp / PASSO_ANALISE)))
                    for k in range(n):
                        t = k / float(n)
                        pontos.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
                        lado.append(i)
                if len(pontos) < 3:
                    continue
                m = len(pontos)
                livre = [not na_divisa(pontos[i], pontos[(i + 1) % m]) for i in range(m)]
                if DECIDIR_POR_LADO:
                    # cada parede e decidida inteira: vale o que acontece na maior parte dela
                    total, solto = {}, {}
                    for i in range(m):
                        a, b = pontos[i], pontos[(i + 1) % m]
                        c = math.hypot(b[0] - a[0], b[1] - a[1])
                        total[lado[i]] = total.get(lado[i], 0.0) + c
                        if livre[i]:
                            solto[lado[i]] = solto.get(lado[i], 0.0) + c
                    for i in range(m):
                        livre[i] = solto.get(lado[i], 0.0) > total[lado[i]] / 2.0
                    if COLINEAR_TUDO_LIVRE:
                        DescontoBeiralPlugin._juntar_colineares(anel, lado, livre, colada)
                DescontoBeiralPlugin._esticar(pontos, livre)

                if all(livre):     # anel inteiro sem divisa -> uma linha fechada
                    seq = [QgsPointXY(*pontos[i]) for i in range(m)] + [QgsPointXY(*pontos[0])]
                    linhas.append((QgsGeometry.fromPolylineXY(seq), None, None))
                    continue
                if not any(livre):
                    continue
                # 2. agrupa trechos livres seguidos (comecando depois de uma face na divisa)
                def rumo(i, para_tras=False):
                    a, b = pontos[i], pontos[(i + 1) % m]
                    if para_tras:
                        a, b = b, a
                    c = math.hypot(b[0] - a[0], b[1] - a[1])
                    return None if c < 1e-12 else ((b[0] - a[0]) / c, (b[1] - a[1]) / c)

                ini = next(i for i in range(m) if not livre[i])
                atual, rumo_ini = [], None
                for j in range(m):
                    i = (ini + 1 + j) % m
                    if livre[i]:
                        if not atual:
                            atual = [QgsPointXY(*pontos[i])]
                            rumo_ini = rumo((i - 1) % m, para_tras=True)   # divisa antes
                        atual.append(QgsPointXY(*pontos[(i + 1) % m]))
                    elif atual:
                        linhas.append((QgsGeometry.fromPolylineXY(atual), rumo_ini, rumo(i)))
                        atual = []
                if atual:
                    linhas.append((QgsGeometry.fromPolylineXY(atual), rumo_ini, rumo(ini)))
        return linhas

    @staticmethod
    def _esticar_nas_pontas(linha, rumo_ini=None, rumo_fim=None):
        """Prolonga as duas pontas de uma sequencia de faces livres (elas sempre
        terminam onde comeca a divisa) e devolve as faixas desse prolongamento.
        Nao prolonga quando a divisa continua quase na mesma direcao da parede
        (ali o prolongamento entraria pela parede da divisa adentro)."""
        pts = linha.asPolyline()
        if len(pts) < 2:
            return []
        if pts[0] == pts[-1]:
            return []                  # anel fechado: nao tem ponta
        faixas = []
        for base, viz, rumo_div in ((pts[0], pts[1], rumo_ini), (pts[-1], pts[-2], rumo_fim)):
            dx, dy = base.x() - viz.x(), base.y() - viz.y()
            comp = math.hypot(dx, dy)
            if comp < 1e-9:
                continue
            if rumo_div is not None:
                cos = (dx / comp) * rumo_div[0] + (dy / comp) * rumo_div[1]
                if cos > math.cos(math.radians(ANGULO_MIN_ESTICAR)):
                    continue           # divisa segue reto: nao estica
            fim = QgsPointXY(base.x() + dx / comp * EXTENSAO_NA_DIVISA,
                             base.y() + dy / comp * EXTENSAO_NA_DIVISA)
            trecho = QgsGeometry.fromPolylineXY([base, fim])
            faixas.append(trecho.buffer(DISTANCIA, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO))
        return faixas

    @staticmethod
    def _juntar_colineares(anel, lado, livre, colada=None):
        """Lados seguidos quase alinhados (vertice colinear no meio da parede) sao a
        mesma parede: se qualquer um deles recebe recuo, a parede inteira recebe -
        menos o lado que esta realmente colado (na linha do lote ou na vizinha)."""
        ordem = []
        for l in lado:
            if not ordem or ordem[-1] != l:
                ordem.append(l)
        if len(ordem) > 1 and ordem[0] == ordem[-1]:
            ordem.pop()
        if len(ordem) < 2:
            return

        def rumo(i):
            return math.atan2(anel.yAt(i + 1) - anel.yAt(i), anel.xAt(i + 1) - anel.xAt(i))

        def virada(a, b):
            d = math.degrees(rumo(b) - rumo(a))
            return abs((d + 180.0) % 360.0 - 180.0)

        n = len(ordem)
        quinas = [k for k in range(n) if virada(ordem[k - 1], ordem[k]) >= ANGULO_COLINEAR]
        if not quinas:
            return                              # anel todo "reto": nao tem o que juntar
        livre_lado = {}
        for i, l in enumerate(lado):
            livre_lado[l] = livre_lado.get(l, False) or livre[i]
        grupo_de = {}
        for q_idx, q in enumerate(quinas):
            fim = quinas[(q_idx + 1) % len(quinas)]
            k = q
            grupo = []
            while True:
                grupo.append(ordem[k])
                k = (k + 1) % n
                if k == fim:
                    break
            if len(grupo) > 1 and any(livre_lado[g] for g in grupo):
                for g in grupo:
                    if livre_lado[g]:
                        continue
                    p1 = (anel.xAt(g), anel.yAt(g))
                    p2 = (anel.xAt(g + 1), anel.yAt(g + 1))
                    if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < COMPRIMENTO_MIN_DIVISA:
                        continue           # pedacinho: nao vale criar faixa so por ele
                    if colada is not None and colada(p1, p2):
                        continue           # parede colada de verdade: continua sem recuo
                    grupo_de[g] = True
        if grupo_de:
            for i, l in enumerate(lado):
                if grupo_de.get(l):
                    livre[i] = True

    @staticmethod
    def _esticar(pontos, livre):
        """Um pedacinho encostado na divisa (bico, dente) no meio de faces livres nao
        deve interromper o recuo: trechos de divisa menores que COMPRIMENTO_MIN_DIVISA
        passam a contar como livres, e o beiral segue em frente."""
        m = len(livre)
        if all(livre) or not any(livre):
            return
        comp = [math.hypot(pontos[(i + 1) % m][0] - pontos[i][0],
                           pontos[(i + 1) % m][1] - pontos[i][1]) for i in range(m)]
        ini = next(i for i in range(m) if livre[i])       # comeca depois de uma face livre
        grupo, total = [], 0.0
        for j in range(m + 1):
            i = (ini + j) % m
            if j < m and not livre[i]:
                grupo.append(i)
                total += comp[i]
            elif grupo:
                if total < COMPRIMENTO_MIN_DIVISA:
                    for k in grupo:
                        livre[k] = True
                grupo, total = [], 0.0

    @staticmethod
    def _tirar_fios(parte):
        """Desfaz o fio de largura quase nula que as vezes fecha o beiral no lado da
        divisa (o furo interno encosta no contorno externo e vira uma linha)."""
        if parte is None or parte.isEmpty():
            return parte
        ab = parte.constGet()
        if not hasattr(ab, "numInteriorRings") or ab.numInteriorRings() == 0:
            return parte
        ext = QgsGeometry(ab.exteriorRing().clone())
        encosta = False
        for k in range(ab.numInteriorRings()):
            furo = QgsGeometry(ab.interiorRing(k).clone())
            if furo.distance(ext) < LARGURA_FIO:
                encosta = True
                break
        if not encosta:
            return parte
        r = LARGURA_FIO / 2.0
        aberto = parte.buffer(-r, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO)
        if aberto is None or aberto.isEmpty():
            return parte
        aberto = aberto.buffer(r, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO)
        if aberto is None or aberto.isEmpty() or not aberto.isGeosValid():
            return parte
        aberto = aberto.intersection(parte)
        if aberto is None or aberto.isEmpty() or not aberto.isGeosValid():
            return parte
        if abs(aberto.area() - parte.area()) > AREA_MAX_ESPINHO:
            return parte
        return aberto

    @staticmethod
    def _tirar_espinhos(geom):
        """Apaga os espinhos: trechos em que o contorno vai e volta pela mesma linha,
        com largura quase nula. Nao muda a area, so limpa os vertices que sobraram
        das operacoes de recorte."""
        if geom is None or geom.isEmpty():
            return geom

        def limpa(pts):
            mudou = True
            while mudou and len(pts) >= 3:
                mudou = False
                # tira pontos repetidos
                i = 0
                while i < len(pts) and len(pts) >= 3:
                    a, b = pts[i], pts[(i + 1) % len(pts)]
                    if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
                        pts.pop((i + 1) % len(pts))
                        mudou = True
                    else:
                        i += 1
                # tira as pontas onde o contorno volta sobre si mesmo
                i = 0
                while i < len(pts) and len(pts) >= 3:
                    a, p, b = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
                    v1 = (p[0] - a[0], p[1] - a[1])
                    v2 = (b[0] - p[0], b[1] - p[1])
                    c1 = math.hypot(*v1)
                    c2 = math.hypot(*v2)
                    if c1 < 1e-12 or c2 < 1e-12:
                        i += 1
                        continue
                    cruz = abs(v1[0] * v2[1] - v1[1] * v2[0])
                    esc = v1[0] * v2[0] + v1[1] * v2[1]
                    if esc < 0 and cruz / max(c1, c2) < LARGURA_ESPINHO:
                        pts.pop(i)
                        mudou = True
                    else:
                        i += 1
            return pts

        def anel_limpo(anel):
            pts = [(anel.xAt(i), anel.yAt(i)) for i in range(anel.numPoints() - 1)]
            pts = limpa(pts)
            if len(pts) < 3:
                return None
            return QgsLineString([QgsPoint(x, y) for x, y in pts] +
                                 [QgsPoint(pts[0][0], pts[0][1])])

        ab = geom.constGet()
        partes = ([ab.geometryN(i) for i in range(ab.numGeometries())]
                  if isinstance(ab, QgsGeometryCollection) else [ab])
        novas = []
        for parte in partes:
            ext = anel_limpo(parte.exteriorRing())
            if ext is None:
                continue
            poli = QgsPolygon()
            poli.setExteriorRing(ext)
            for k in range(parte.numInteriorRings()):
                furo = anel_limpo(parte.interiorRing(k))
                if furo is not None:
                    poli.addInteriorRing(furo)
            novas.append(poli)
        if not novas:
            return geom
        if len(novas) == 1:
            saida = QgsGeometry(novas[0])
        else:
            mp = QgsMultiPolygon()
            for poli in novas:
                mp.addGeometry(poli)
            saida = QgsGeometry(mp)
        # o espinho tem area quase nula, mas nao exatamente zero
        if not saida.isGeosValid() or abs(saida.area() - geom.area()) > AREA_MAX_ESPINHO:
            return geom
        return saida

    @staticmethod
    def _tirar_vertices_extras(nova, *refs):
        """Remove os vertices colineares criados pelo calculo das faixas, mas
        preserva todos os vertices da edificacao original (para nao perder a
        coincidencia com o lote e com as camadas vizinhas)."""
        originais = set()
        for ref in refs:
            if ref is None or ref.isEmpty():
                continue
            ab = ref.constGet()
            partes = ([ab.geometryN(i) for i in range(ab.numGeometries())]
                      if isinstance(ab, QgsGeometryCollection) else [ab])
            for parte in partes:
                aneis = [parte.exteriorRing()] + [parte.interiorRing(k)
                                                  for k in range(parte.numInteriorRings())]
                for anel in aneis:
                    if anel is None:
                        continue
                    for i in range(anel.numPoints()):
                        originais.add((round(anel.xAt(i), 9), round(anel.yAt(i), 9)))

        def limpa_anel(anel):
            pts = [(anel.xAt(i), anel.yAt(i)) for i in range(anel.numPoints() - 1)]
            saida = []
            for i, p in enumerate(pts):
                if (round(p[0], 9), round(p[1], 9)) in originais:
                    saida.append(p)
                    continue
                a = saida[-1] if saida else pts[i - 1]
                b = pts[(i + 1) % len(pts)]
                dx, dy = b[0] - a[0], b[1] - a[1]
                comp = math.hypot(dx, dy)
                if comp < 1e-12:
                    saida.append(p)
                    continue
                dist = abs(dx * (a[1] - p[1]) - dy * (a[0] - p[0])) / comp   # ponto-reta
                if dist > TOLERANCIA_SIMPLIFICACAO:
                    saida.append(p)
            if len(saida) < 3:
                return None
            return QgsLineString([QgsPoint(x, y) for x, y in saida] +
                                 [QgsPoint(saida[0][0], saida[0][1])])

        ab = nova.constGet()
        partes = ([ab.geometryN(i) for i in range(ab.numGeometries())]
                  if isinstance(ab, QgsGeometryCollection) else [ab])
        novas = []
        for parte in partes:
            ext = limpa_anel(parte.exteriorRing())
            if ext is None:
                continue
            poli = QgsPolygon()
            poli.setExteriorRing(ext)
            for k in range(parte.numInteriorRings()):
                furo = limpa_anel(parte.interiorRing(k))
                if furo is not None:
                    poli.addInteriorRing(furo)
            novas.append(poli)
        if not novas:
            return None
        if len(novas) == 1:
            saida = QgsGeometry(novas[0])
        else:
            mp = QgsMultiPolygon()
            for poli in novas:
                mp.addGeometry(poli)
            saida = QgsGeometry(mp)
        if not saida.isGeosValid() or abs(saida.area() - nova.area()) > 1e-6:
            return nova            # seguranca: se mexeu na area, devolve como estava
        return saida

    @staticmethod
    def _aparar_beiral(beiral):
        """Corta as pontas em agulha (mais finas que LARGURA_MIN_BEIRAL) e descarta
        os fragmentos menores que AREA_MIN_BEIRAL. O que sai daqui volta a ser
        edificacao, entao as duas camadas continuam se encaixando."""
        if beiral is None or beiral.isEmpty():
            return None
        r = LARGURA_MIN_BEIRAL / 2.0
        aberto = beiral.buffer(-r, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO)
        if aberto is not None and not aberto.isEmpty():
            aberto = aberto.buffer(r, 12, PONTA_RETA, CANTO_VIVO, LIMITE_CANTO)
        if aberto is None or aberto.isEmpty() or not aberto.isGeosValid():
            aberto = beiral
        else:
            aberto = aberto.intersection(beiral)      # nunca passar do beiral original
        partes = [p for p in aberto.asGeometryCollection()
                  if p.type() == _tipo_poligono() and p.area() >= AREA_MIN_BEIRAL]
        if not partes:
            return None
        return partes[0] if len(partes) == 1 else QgsGeometry.collectGeometry(partes)

    @staticmethod
    def _limpar(geom):
        """Descarta lascas (partes minusculas) do resultado."""
        partes = [p for p in geom.asGeometryCollection()
                  if p.type() == _tipo_poligono() and p.area() >= AREA_MIN_PARTE]
        if not partes:
            return None
        if len(partes) == 1:
            return partes[0]
        return QgsGeometry.collectGeometry(partes)

    @staticmethod
    def _repor_z(nova, orig):
        """Operacoes do GEOS perdem o Z: repoe o Z do primeiro vertice da original."""
        z = 0.0
        try:
            v = orig.constGet().vertexAt(QgsVertexId(0, 0, 0))
            if v.is3D() and not math.isnan(v.z()):
                z = v.z()
        except Exception:
            pass
        nova.get().dropZValue()
        nova.get().addZValue(z)

    # ------------------------------------------------------------------
    def _relatorio(self, camada, beiral, qtd_ok, qtd_beiral, erros, avisos, editou):
        linhas = []
        if editou:
            linhas.append("%d edificacao(oes) com beiral descontado na camada '%s'."
                          % (qtd_ok, camada.name()))
            if beiral is not None:
                linhas.append("%d poligono(s) de beiral adicionado(s) na camada '%s'."
                              % (qtd_beiral, beiral.name()))
            linhas.append("")
            linhas.append("NADA FOI SALVO. Confira o resultado e clique em 'Salvar edicoes' "
                          "nas camadas - ou desfaca com Ctrl+Z / 'Descartar edicoes'.")
        else:
            linhas.append("Nenhuma feicao foi alterada.")
        if erros:
            linhas.append("")
            linhas.append("Nao alteradas (%d):" % len(erros))
            linhas += ["  - ID %s: %s" % (fid, motivo) for fid, motivo in erros[:30]]
            if len(erros) > 30:
                linhas.append("  ... e mais %d" % (len(erros) - 30))
        if avisos:
            linhas.append("")
            linhas.append("Avisos:")
            linhas += ["  - " + a for a in avisos[:30]]

        texto = "\n".join(linhas)
        if erros or not editou:
            QMessageBox.warning(self.iface.mainWindow(), TITULO, texto)
        else:
            extra = (" + %d beiral(is) em '%s'" % (qtd_beiral, beiral.name())) if beiral else ""
            self.iface.messageBar().pushMessage(
                TITULO,
                "%d edificacao(oes) alterada(s)%s. NAO SALVO - confira e salve manualmente."
                % (qtd_ok, extra),
                level=NIVEL_SUCESSO, duration=10)
