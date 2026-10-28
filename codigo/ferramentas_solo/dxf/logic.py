import os
import re
import sys
import subprocess
import gc
import unicodedata

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)


class FeedbackSimples:
    """Substitui o QgsProcessingFeedback nas rotinas originais (que foram
    escritas como QgsProcessingAlgorithm) para que possam ser chamadas
    diretamente por um botão de barra de ferramentas, sem precisar do
    Processing Toolbox."""

    def __init__(self):
        self.mensagens = []

    def pushInfo(self, msg):
        self.mensagens.append(msg)

    def pushConsoleInfo(self, msg):
        self.mensagens.append(msg)

    def reportError(self, msg, fatal=False):
        self.mensagens.append(msg)
        if fatal:
            raise RuntimeError(msg)

    def setProgress(self, valor):
        pass

    def isCanceled(self):
        return False


def garantir_ezdxf(feedback):
    """Importa a biblioteca ezdxf, instalando via pip se necessário
    (mesmo comportamento das 4 ferramentas originais)."""
    try:
        import ezdxf
        return ezdxf
    except ImportError:
        feedback.pushInfo("A biblioteca 'ezdxf' não foi encontrada. Iniciando instalação automática...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "ezdxf"])
            feedback.pushInfo("Biblioteca 'ezdxf' instalada com sucesso! Continuando...")
            import ezdxf
            return ezdxf
        except Exception as e:
            raise RuntimeError(f"Não foi possível instalar a biblioteca 'ezdxf'. Detalhe: {str(e)}")


def validar_arquivo(caminho):
    if not os.path.exists(caminho):
        raise RuntimeError("Arquivo não encontrado!")
    if caminho.lower().endswith(".dwg"):
        raise RuntimeError("A ferramenta lê apenas DXF. Salve seu arquivo como .dxf no CAD.")


# ---------------------------------------------------------------------------
# Onde as feições importadas ficam salvas
# ---------------------------------------------------------------------------
# chave: (rótulo, driver OGR, extensão, destino é um arquivo único?)
FORMATOS = {
    "gpkg": ("GeoPackage (.gpkg)", "GPKG", ".gpkg", True),
    "shp": ("Shapefile (.shp)", "ESRI Shapefile", ".shp", False),
    "geojson": ("GeoJSON (.geojson)", "GeoJSON", ".geojson", False),
    "memoria": ("Camada temporária (não salva)", None, None, False),
}
ORDEM_FORMATOS = ("gpkg", "shp", "geojson", "memoria")


class Cancelado(Exception):
    """O usuário desistiu (ex.: na pergunta sobre camadas que já existem)."""


class Destino:
    """formato: chave de FORMATOS. caminho: arquivo .gpkg ou pasta (shp/geojson); vazio = mesma
    pasta e mesmo nome do DXF. ao_existir(nomes, onde) -> "substituir", "renomear" ou None
    (cancelar); sem ele, cria com outro nome."""

    def __init__(self, formato="memoria", caminho="", ao_existir=None):
        self.formato = formato if formato in FORMATOS else "memoria"
        self.caminho = (caminho or "").strip()
        self.ao_existir = ao_existir

    def alvo(self, caminho_dxf):
        """Arquivo .gpkg ou pasta onde as camadas vão ser gravadas."""
        _rot, _drv, ext, arquivo_unico = FORMATOS[self.formato]
        if ext is None:
            return ""
        if arquivo_unico:
            alvo = self.caminho or os.path.splitext(caminho_dxf)[0] + ext
            if not alvo.lower().endswith(ext):
                alvo += ext
            return alvo
        return self.caminho or os.path.dirname(os.path.abspath(caminho_dxf))


def nome_seguro(texto):
    """Nome de tabela/arquivo sem acento, espaço nem caractere especial."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^A-Za-z0-9_-]+", "_", t).strip("_")
    return t or "camada"


def _acao(nome):
    try:
        return getattr(QgsVectorFileWriter, nome)
    except AttributeError:
        return getattr(QgsVectorFileWriter.ActionOnExistingFile, nome)


def _sem_erro(erro):
    return getattr(erro, "value", erro) == 0


def _tabelas_gpkg(arquivo):
    if not os.path.exists(arquivo):
        return set()
    try:
        from qgis.core import QgsProviderRegistry
        return {d.name().lower() for d in QgsProviderRegistry.instance().querySublayers(arquivo)}
    except Exception:
        return set()


def _sem_repetir(nome, usados):
    if nome.lower() not in usados:
        return nome
    n = 2
    while ("%s_%d" % (nome, n)).lower() in usados:
        n += 1
    return "%s_%d" % (nome, n)


def _gravar(camadas, caminho_dxf, destino, feedback):
    """Grava as camadas de memória no formato escolhido e devolve as camadas do arquivo."""
    rotulo, driver, ext, arquivo_unico = FORMATOS[destino.formato]
    alvo = destino.alvo(caminho_dxf)
    if arquivo_unico:
        pasta = os.path.dirname(os.path.abspath(alvo))
        existentes = _tabelas_gpkg(alvo)
    else:
        pasta = alvo
        existentes = None
    try:
        os.makedirs(pasta, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("Não foi possível criar a pasta de destino:\n%s\n%s" % (pasta, exc))
    prefixo = "" if arquivo_unico else nome_seguro(os.path.splitext(os.path.basename(caminho_dxf))[0]) + "_"

    def existe(nome):
        if arquivo_unico:
            return nome.lower() in existentes
        return any(os.path.exists(os.path.join(pasta, nome + e)) for e in (ext, ext.upper()))

    # nomes sem repetir dentro da própria importação (ex.: layers "LOTE 1" e "LOTE_1")
    bases, nomes, usados = [], [], set()
    for _vl, _exib, base in camadas:
        base = prefixo + nome_seguro(base)
        nome = _sem_repetir(base, usados)
        usados.add(nome.lower())
        bases.append(base)
        nomes.append(nome)

    conflitos = [n for n in nomes if existe(n)]
    acao = "renomear"
    if conflitos:
        onde = alvo if arquivo_unico else pasta
        acao = destino.ao_existir(conflitos, onde) if destino.ao_existir else "renomear"
        if acao is None:
            raise Cancelado()
    if acao == "renomear" and conflitos:
        # os que já existem ganham _2, _3...; os outros mantêm o nome
        ocupados = {n.lower() for n in nomes}
        finais = []
        for base, nome in zip(bases, nomes):
            if not existe(nome):
                finais.append(nome)
                continue
            n = 2
            while existe("%s_%d" % (base, n)) or ("%s_%d" % (base, n)).lower() in ocupados:
                n += 1
            novo = "%s_%d" % (base, n)
            ocupados.add(novo.lower())
            finais.append(novo)
        nomes = finais

    contexto = QgsProject.instance().transformContext()
    saidas, falhas = [], []
    for (vl, exib, _base), nome in zip(camadas, nomes):
        op = QgsVectorFileWriter.SaveVectorOptions()
        op.driverName = driver
        op.fileEncoding = "UTF-8"
        if arquivo_unico:
            arquivo = alvo
            op.layerName = nome
            # arquivo novo: cria; arquivo que já existe: só cria/substitui a tabela (nunca apaga o resto)
            op.actionOnExistingFile = _acao("CreateOrOverwriteLayer" if os.path.exists(alvo)
                                            else "CreateOrOverwriteFile")
            fonte = "%s|layername=%s" % (arquivo, nome)
        else:
            arquivo = os.path.join(pasta, nome + ext)
            op.actionOnExistingFile = _acao("CreateOrOverwriteFile")
            fonte = arquivo
        res = QgsVectorFileWriter.writeAsVectorFormatV3(vl, arquivo, contexto, op)
        if not _sem_erro(res[0]):
            falhas.append("%s: %s" % (nome, res[1] or "erro ao gravar (o arquivo pode estar aberto em outro programa)"))
            continue
        nova = QgsVectorLayer(fonte, exib, "ogr")
        if not nova.isValid():
            falhas.append("%s: gravado, mas não abriu no QGIS" % nome)
            continue
        saidas.append(nova)
        if acao == "substituir":
            _recarregar_iguais(fonte)

    if falhas and not saidas:
        raise RuntimeError("Nada foi gravado em %s:\n%s" % (alvo, "\n".join(falhas)))
    texto = "\nSalvo em %s: %s" % (rotulo.split(" (")[0], alvo)
    if not arquivo_unico:
        texto += "  (%d arquivo(s) %s)" % (len(saidas), ext)
    if falhas:
        texto += "\n\nNão foi possível gravar:\n" + "\n".join(falhas)
    return saidas, texto


def _normal(fonte):
    partes = fonte.split("|")
    return "|".join([os.path.normcase(os.path.abspath(partes[0]))] + [p.lower() for p in partes[1:]])


def _recarregar_iguais(fonte):
    """Camadas do projeto que já usavam a tabela/arquivo substituído passam a mostrar o novo conteúdo."""
    alvo = _normal(fonte)
    for lyr in QgsProject.instance().mapLayers().values():
        try:
            if isinstance(lyr, QgsVectorLayer) and _normal(lyr.source()) == alvo:
                lyr.reload()
                lyr.updateExtents()
                lyr.triggerRepaint()
        except Exception:
            pass


def entregar(camadas, nome_grupo, caminho_dxf, destino, feedback):
    """Coloca as camadas importadas no projeto, salvando antes no formato escolhido.
    camadas: lista de (camada de memória, nome no painel, nome base da tabela/arquivo).
    Devolve o texto a acrescentar na mensagem final."""
    destino = destino or Destino()
    if destino.formato == "memoria":
        finais, texto = [vl for vl, _e, _b in camadas], ""
    else:
        finais, texto = _gravar(camadas, caminho_dxf, destino, feedback)
    project = QgsProject.instance()
    if nome_grupo:
        grupo = project.layerTreeRoot().addGroup(nome_grupo)
        for vl in finais:
            project.addMapLayer(vl, False)
            grupo.addLayer(vl)
    else:
        for vl in finais:
            project.addMapLayer(vl)
    return texto


# ---------------------------------------------------------------------------
# 1) Importar Pontos / Vértices / Blocos / Centro de Círculos
#    (adaptado de ImportarPontosVerticesBlocosCAD.py)
# ---------------------------------------------------------------------------
def importar_pontos(caminho, feedback, destino=None):
    validar_arquivo(caminho)
    ezdxf = garantir_ezdxf(feedback)

    doc = None
    msp = None
    entities = None
    try:
        doc = ezdxf.readfile(caminho)
        msp = doc.modelspace()

        entities = msp.query('POINT LWPOLYLINE POLYLINE INSERT CIRCLE')
        total = len(entities)

        if total == 0:
            return "Nenhuma entidade compatível encontrada no arquivo."

        pontos_por_layer = {}

        for i, entity in enumerate(entities):
            if feedback.isCanceled():
                break
            feedback.setProgress(int((i / total) * 100))

            layer_cad = entity.dxf.layer
            if layer_cad not in pontos_por_layer:
                pontos_por_layer[layer_cad] = []

            if entity.dxftype() == 'POINT':
                loc = entity.dxf.location
                pontos_por_layer[layer_cad].append(QgsPointXY(loc.x, loc.y))
            elif entity.dxftype() == 'LWPOLYLINE':
                for point in entity.vertices():
                    pontos_por_layer[layer_cad].append(QgsPointXY(point[0], point[1]))
            elif entity.dxftype() == 'POLYLINE':
                if getattr(entity, 'is_2d_polyline', False) or getattr(entity, 'is_3d_polyline', False):
                    for v in entity.vertices:
                        loc = v.dxf.location
                        pontos_por_layer[layer_cad].append(QgsPointXY(loc.x, loc.y))
            elif entity.dxftype() == 'INSERT':
                loc = entity.dxf.insert
                pontos_por_layer[layer_cad].append(QgsPointXY(loc.x, loc.y))
            elif entity.dxftype() == 'CIRCLE':
                # centroide do círculo = centro (coordenadas WCS; trata OCS/extrusão)
                try:
                    c = entity.ocs().to_wcs(entity.dxf.center)
                except Exception:
                    c = entity.dxf.center
                pontos_por_layer[layer_cad].append(QgsPointXY(c.x, c.y))

        pontos_por_layer = {k: v for k, v in pontos_por_layer.items() if len(v) > 0}

        if not pontos_por_layer:
            return "Nenhum ponto válido extraído."

        project = QgsProject.instance()
        crs = project.crs().authid()

        nome_grupo = os.path.basename(caminho).split('.')[0] + " - Pontos CAD"
        camadas = []

        total_pontos = 0
        for nome_layer, lista_pontos in pontos_por_layer.items():
            vl = QgsVectorLayer(f"Point?crs={crs}", f"Layer: {nome_layer}", "memory")
            pr = vl.dataProvider()
            pr.addAttributes([QgsField("Layer_CAD", QVariant.String)])
            vl.updateFields()

            features = []
            for pt in lista_pontos:
                f = QgsFeature(vl.fields())
                f.setGeometry(QgsGeometry.fromPointXY(pt))
                f.setAttributes([nome_layer])
                features.append(f)

            pr.addFeatures(features)
            vl.updateExtents()

            camadas.append((vl, vl.name(), f"{nome_layer}_pontos"))
            total_pontos += len(lista_pontos)

        salvo = entregar(camadas, nome_grupo, caminho, destino, feedback)
        return f"Sucesso! {total_pontos} pontos (incluindo blocos e centros de círculos) divididos em {len(pontos_por_layer)} camadas." + salvo

    finally:
        if doc is not None:
            del doc
        if msp is not None:
            del msp
        if entities is not None:
            del entities
        gc.collect()


# ---------------------------------------------------------------------------
# 2) Importar Textos
#    (adaptado de importardwgtextos.py) - cria uma única camada de pontos
#    com os atributos Texto e Layer_CAD, adicionada diretamente ao projeto
# ---------------------------------------------------------------------------
def importar_textos(caminho, feedback, destino=None):
    validar_arquivo(caminho)
    ezdxf = garantir_ezdxf(feedback)

    doc = None
    msp = None
    text_entities = None
    try:
        doc = ezdxf.readfile(caminho)
        msp = doc.modelspace()

        crs = QgsProject.instance().crs().authid()
        vl = QgsVectorLayer(f"Point?crs={crs}", os.path.basename(caminho).split('.')[0] + " - Textos", "memory")
        pr = vl.dataProvider()
        pr.addAttributes([
            QgsField("Texto", QVariant.String),
            QgsField("Layer_CAD", QVariant.String),
        ])
        vl.updateFields()

        text_entities = msp.query('TEXT MTEXT')
        total = len(text_entities)

        if total == 0:
            return "Nenhum texto encontrado no arquivo."

        features = []
        for i, entity in enumerate(text_entities):
            if feedback.isCanceled():
                break
            feedback.setProgress(int((i / total) * 100))

            if entity.dxftype() == 'MTEXT':
                texto_puro = entity.text.replace('\\P', ' ')
            else:
                texto_puro = entity.dxf.text

            ponto_insercao = entity.dxf.insert

            f = QgsFeature(vl.fields())
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(ponto_insercao.x, ponto_insercao.y)))
            f.setAttributes([texto_puro, entity.dxf.layer])
            features.append(f)

        pr.addFeatures(features)
        vl.updateExtents()
        salvo = entregar([(vl, vl.name(), "textos")], None, caminho, destino, feedback)

        return f"Sucesso! {total} textos importados." + salvo

    finally:
        if doc is not None:
            del doc
        if msp is not None:
            del msp
        if text_entities is not None:
            del text_entities
        gc.collect()


# ---------------------------------------------------------------------------
# 3) Importar Linhas
#    (adaptado de importarlinhas.py)
# ---------------------------------------------------------------------------
def importar_linhas(caminho, feedback, destino=None):
    validar_arquivo(caminho)
    ezdxf = garantir_ezdxf(feedback)

    doc = None
    msp = None
    entities = None
    try:
        doc = ezdxf.readfile(caminho)
        msp = doc.modelspace()

        entities = msp.query('LINE LWPOLYLINE POLYLINE')
        total = len(entities)

        if total == 0:
            return "Nenhuma linha ou polilinha encontrada no arquivo."

        linhas_por_layer = {}

        for i, entity in enumerate(entities):
            if feedback.isCanceled():
                break
            feedback.setProgress(int((i / total) * 100))

            layer_cad = entity.dxf.layer
            pts = []

            if entity.dxftype() == 'LINE':
                pts.append(QgsPointXY(entity.dxf.start.x, entity.dxf.start.y))
                pts.append(QgsPointXY(entity.dxf.end.x, entity.dxf.end.y))
            elif entity.dxftype() in ['LWPOLYLINE', 'POLYLINE']:
                is_closed = False
                if hasattr(entity, 'closed'):
                    is_closed = bool(entity.closed)
                elif hasattr(entity, 'is_closed'):
                    is_closed = bool(entity.is_closed)

                if is_closed:
                    continue

                if entity.dxftype() == 'LWPOLYLINE':
                    for point in entity.vertices():
                        pts.append(QgsPointXY(point[0], point[1]))
                else:
                    if getattr(entity, 'is_2d_polyline', False) or getattr(entity, 'is_3d_polyline', False):
                        for v in entity.vertices:
                            loc = v.dxf.location
                            pts.append(QgsPointXY(loc.x, loc.y))

                if len(pts) > 2 and pts[0] == pts[-1]:
                    continue

            if len(pts) >= 2:
                geom = QgsGeometry.fromPolylineXY(pts)
                if geom.isGeosValid():
                    if layer_cad not in linhas_por_layer:
                        linhas_por_layer[layer_cad] = []
                    linhas_por_layer[layer_cad].append(geom)

        if not linhas_por_layer:
            return "Nenhuma linha aberta válida foi encontrada."

        project = QgsProject.instance()
        crs = project.crs().authid()

        nome_grupo = os.path.basename(caminho).split('.')[0] + " - Linhas"
        camadas = []

        total_feicoes = 0
        for nome_layer, geometrias in linhas_por_layer.items():
            vl = QgsVectorLayer(f"LineString?crs={crs}", f"Layer: {nome_layer}", "memory")
            pr = vl.dataProvider()
            pr.addAttributes([QgsField("Layer_CAD", QVariant.String)])
            vl.updateFields()

            features = []
            for geom in geometrias:
                f = QgsFeature(vl.fields())
                f.setGeometry(geom)
                f.setAttributes([nome_layer])
                features.append(f)

            pr.addFeatures(features)
            vl.updateExtents()

            camadas.append((vl, vl.name(), f"{nome_layer}_linhas"))
            total_feicoes += len(geometrias)

        salvo = entregar(camadas, nome_grupo, caminho, destino, feedback)
        return f"Sucesso! {total_feicoes} linhas importadas e divididas em {len(linhas_por_layer)} camadas." + salvo

    finally:
        if doc is not None:
            del doc
        if msp is not None:
            del msp
        if entities is not None:
            del entities
        gc.collect()


# ---------------------------------------------------------------------------
# 4) Importar Polígonos
#    (adaptado de importarpoligonoscad.py)
# ---------------------------------------------------------------------------
def importar_poligonos(caminho, feedback, destino=None):
    validar_arquivo(caminho)
    ezdxf = garantir_ezdxf(feedback)

    doc = None
    msp = None
    entities = None
    try:
        doc = ezdxf.readfile(caminho)
        msp = doc.modelspace()

        entities = msp.query('LWPOLYLINE POLYLINE')
        total = len(entities)

        if total == 0:
            return "Nenhuma Polyline encontrada no arquivo."

        poligonos_por_layer = {}

        for i, entity in enumerate(entities):
            if feedback.isCanceled():
                break
            feedback.setProgress(int((i / total) * 100))

            layer_cad = entity.dxf.layer
            pts = []

            if entity.dxftype() == 'LWPOLYLINE':
                for point in entity.vertices():
                    pts.append(QgsPointXY(point[0], point[1]))
            elif entity.dxftype() == 'POLYLINE':
                if getattr(entity, 'is_2d_polyline', False) or getattr(entity, 'is_3d_polyline', False):
                    for v in entity.vertices:
                        loc = v.dxf.location
                        pts.append(QgsPointXY(loc.x, loc.y))

            if len(pts) < 3:
                continue

            is_closed = False
            if hasattr(entity, 'closed'):
                is_closed = bool(entity.closed)
            elif hasattr(entity, 'is_closed'):
                is_closed = bool(entity.is_closed)

            if not is_closed and pts[0] != pts[-1]:
                continue

            if pts[0] != pts[-1]:
                pts.append(pts[0])

            geom = QgsGeometry.fromPolygonXY([pts])

            if geom.isGeosValid():
                if layer_cad not in poligonos_por_layer:
                    poligonos_por_layer[layer_cad] = []
                poligonos_por_layer[layer_cad].append(geom)

        if not poligonos_por_layer:
            return "Nenhum polígono (forma fechada) válido foi encontrado."

        project = QgsProject.instance()
        crs = project.crs().authid()

        nome_grupo = os.path.basename(caminho).split('.')[0] + " - Polígonos"
        camadas = []

        for nome_layer, geometrias in poligonos_por_layer.items():
            vl = QgsVectorLayer(f"Polygon?crs={crs}", f"Layer: {nome_layer}", "memory")
            pr = vl.dataProvider()
            pr.addAttributes([QgsField("Layer_CAD", QVariant.String)])
            vl.updateFields()

            features = []
            for geom in geometrias:
                f = QgsFeature(vl.fields())
                f.setGeometry(geom)
                f.setAttributes([nome_layer])
                features.append(f)

            pr.addFeatures(features)
            vl.updateExtents()

            camadas.append((vl, vl.name(), f"{nome_layer}_poligonos"))

        salvo = entregar(camadas, nome_grupo, caminho, destino, feedback)
        return f"Sucesso! {len(poligonos_por_layer)} camadas diferentes foram importadas." + salvo

    finally:
        if doc is not None:
            del doc
        if msp is not None:
            del msp
        if entities is not None:
            del entities
        gc.collect()


# Registro central usado pelo diálogo: (rótulo exibido, função)
OPCOES = [
    ("Pontos / Vértices / Blocos", importar_pontos),
    ("Textos", importar_textos),
    ("Linhas (abertas)", importar_linhas),
    ("Polígonos (fechados)", importar_poligonos),
]
