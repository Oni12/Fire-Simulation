import os
import xml.etree.ElementTree as ET

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
KML_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "mapa.kml")


def parse_polygons() -> list[list[list[float]]]:
    tree = ET.parse(KML_PATH)
    root = tree.getroot()

    polygons = root.findall(".//kml:Polygon", KML_NS)
    if not polygons:
        raise ValueError("No se encontró ningún <Polygon> en el KML")

    result: list[list[list[float]]] = []
    for polygon in polygons:
        coords_elem = polygon.find(".//kml:coordinates", KML_NS)
        if coords_elem is None or not coords_elem.text:
            continue

        raw = coords_elem.text.strip().replace("\n", " ")
        points: list[list[float]] = []
        for token in raw.split():
            parts = token.split(",")
            if len(parts) >= 2:
                lng = float(parts[0])
                lat = float(parts[1])
                points.append([lat, lng])

        if points:
            result.append(points)

    return result
