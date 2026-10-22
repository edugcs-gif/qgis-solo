import os
from pathlib import Path

from qgis.PyQt.QtCore import QFile, QIODevice
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsProject,
    QgsVectorLayer,
    QgsFillSymbol,
    QgsSingleSymbolRenderer,
    QgsDxfExport
)

# Caminho dinâmico para a Área de Trabalho de qualquer usuário
OUTPUT_DIR = str(Path.home() / "Desktop" / "dxf")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "Exportado(DXF).dxf")


def exportar_selecao(iface):
    """Exporta os polígonos selecionados da camada ativa para OUTPUT_FILE."""
    layer = iface.activeLayer()

    if layer is None:
        iface.messageBar().pushMessage(
            "Exportador DXF",
            "Nenhuma camada ativa. Selecione a camada de polígonos na legenda.",
            level=Qgis.Warning,
            duration=5,
        )
        return

    if layer.type() != layer.VectorLayer:
        iface.messageBar().pushMessage(
            "Exportador DXF",
            "A camada ativa não é uma camada vetorial.",
            level=Qgis.Warning,
            duration=5,
        )
        return

    selected_count = layer.selectedFeatureCount()
    if selected_count == 0:
        iface.messageBar().pushMessage(
            "Exportador DXF",
            "Nenhum polígono selecionado. Selecione ao menos um elemento antes de exportar.",
            level=Qgis.Warning,
            duration=5,
        )
        return

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except OSError as e:
        iface.messageBar().pushMessage(
            "Exportador DXF",
            f"Não foi possível criar a pasta de destino: {e}",
            level=Qgis.Critical,
            duration=8,
        )
        return

    # 1. Cria a camada temporária COMO POLÍGONO
    crs_id = layer.crs().authid()
    temp_layer = QgsVectorLayer(f"Polygon?crs={crs_id}", "temp_export", "memory")
    temp_provider = temp_layer.dataProvider()

    temp_features = []
    for feat in layer.selectedFeatures():
        new_feat = QgsFeature()
        new_feat.setGeometry(feat.geometry())
        temp_features.append(new_feat)

    temp_provider.addFeatures(temp_features)
    temp_layer.updateExtents()

    # 2. O PULO DO GATO: Simbologia de "Apenas Contorno"
    # Configurar 'style': 'no' remove o preenchimento (Brush nulo).
    # Ao ver que não há preenchimento, o QgsDxfExport transforma o polígono
    # numa Polyline com Closed=Yes e sem duplicar o último vértice.
    symbol = QgsFillSymbol.createSimple({
        'outline_color': 'black',
        'style': 'no'  
    })
    temp_layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    # 3. Utiliza o Exportador Nativo do QGIS (QgsDxfExport) em vez do GDAL/OGR
    dxf_export = QgsDxfExport()
    dxf_export.setDestinationCrs(layer.crs())
    dxf_export.setSymbologyScale(1000)
    dxf_export.setSymbologyExport(QgsDxfExport.FeatureSymbology)
    dxf_export.setExtent(temp_layer.extent())

    # Adiciona nossa camada temporária formatada para exportação
    dxf_layer = QgsDxfExport.DxfLayer(temp_layer)
    dxf_export.addLayers([dxf_layer])

    # Criação e abertura do arquivo físico
    dxf_file = QFile(OUTPUT_FILE)
    if not dxf_file.open(QIODevice.WriteOnly | QIODevice.Truncate):
        iface.messageBar().pushMessage(
            "Exportador DXF",
            "Não foi possível abrir o arquivo para gravação.",
            level=Qgis.Critical,
            duration=8,
        )
        return

    # Grava os dados no arquivo
    result = dxf_export.writeToFile(dxf_file, "UTF-8")
    dxf_file.close()

    # O retorno 0 (QgsDxfExport.ExportResult.Success) indica sucesso no exportador nativo
    if result == 0:
        iface.messageBar().pushMessage(
            "Exportador DXF",
            f"{selected_count} elemento(s) exportado(s) com sucesso como Polylines Fechadas para {OUTPUT_FILE}",
            level=Qgis.Success,
            duration=5,
        )
    else:
        iface.messageBar().pushMessage(
            "Exportador DXF",
            f"Erro ao exportar (código {result}).",
            level=Qgis.Critical,
            duration=8,
        )
