import os
import sys
import subprocess
import gc

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsProject,
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
# 1) Importar Pontos / Vértices / Blocos / Centro de Círculos
#    (adaptado de ImportarPontosVerticesBlocosCAD.py)
# ---------------------------------------------------------------------------
def importar_pontos(caminho, feedback):
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
        root = project.layerTreeRoot()

        nome_grupo = os.path.basename(caminho).split('.')[0] + " - Pontos CAD"
        grupo = root.addGroup(nome_grupo)

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

            project.addMapLayer(vl, False)
            grupo.addLayer(vl)
            total_pontos += len(lista_pontos)

        return f"Sucesso! {total_pontos} pontos (incluindo blocos e centros de círculos) divididos em {len(pontos_por_layer)} camadas."

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
def importar_textos(caminho, feedback):
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
        QgsProject.instance().addMapLayer(vl)

        return f"Sucesso! {total} textos importados."

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
def importar_linhas(caminho, feedback):
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
        root = project.layerTreeRoot()

        nome_grupo = os.path.basename(caminho).split('.')[0] + " - Linhas"
        grupo = root.addGroup(nome_grupo)

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

            project.addMapLayer(vl, False)
            grupo.addLayer(vl)
            total_feicoes += len(geometrias)

        return f"Sucesso! {total_feicoes} linhas importadas e divididas em {len(linhas_por_layer)} camadas."

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
def importar_poligonos(caminho, feedback):
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
        root = project.layerTreeRoot()

        nome_grupo = os.path.basename(caminho).split('.')[0] + " - Polígonos"
        grupo = root.addGroup(nome_grupo)

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

            project.addMapLayer(vl, False)
            grupo.addLayer(vl)

        return f"Sucesso! {len(poligonos_por_layer)} camadas diferentes foram importadas."

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
