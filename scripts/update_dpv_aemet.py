from pathlib import Path
from datetime import datetime, timezone
import csv
import io
import json
import math
import re

import requests


URL_AEMET = "https://www.aemet.es/es/eltiempo/observacion/ultimosdatos_comunitat-valenciana_datos-horarios.csv?k=val&datos=det&w=0&f=temperatura&x=h24"

DOCS_DIR = Path("docs")
DOCS_DIR.mkdir(exist_ok=True)

OUT_GEOJSON = DOCS_DIR / "dpv_aemet.geojson"
OUT_METADATA = DOCS_DIR / "metadata_aemet.json"


# Coordenadas aproximadas de estaciones/municipios AEMET.
# Formato: "Estación": [latitud, longitud]
COORDS = {
    "Ademuz": [40.0613, -1.2866],
    "Alacant/Alicante": [38.3452, -0.4810],
    "Alcoi/Alcoy": [38.6987, -0.4737],
    "Alicante-Elche Aeropuerto": [38.2822, -0.5582],
    "Atzeneta del Maestrat": [40.2167, -0.1667],
    "Barx": [39.0133, -0.3017],
    "Bejís": [39.9117, -0.7056],
    "Benidorm": [38.5411, -0.1225],
    "Bicorp": [39.1328, -0.7878],
    "Carcaixent": [39.1217, -0.4489],
    "Castellfort": [40.5028, -0.1917],
    "Castelló - Almassora": [39.9560, -0.0650],
    "Chelva": [39.7494, -0.9967],
    "el Pinós/Pinoso": [38.4011, -1.0414],
    "Elx/Elche": [38.2699, -0.7126],
    "Fontanars dels Alforins": [38.7842, -0.7861],
    "Jalance": [39.1917, -1.0772],
    "Jávea/ Xàbia": [38.7890, 0.1631],
    "La Pobla de Benifassà-Fredes": [40.7092, 0.1697],
    "Llíria": [39.6289, -0.5972],
    "Miramar": [38.9500, -0.1394],
    "Montanejos": [40.0667, -0.5225],
    "Morella": [40.6197, -0.0989],
    "Novelda": [38.3850, -0.7677],
    "Oliva": [38.9190, -0.1194],
    "Ontinyent": [38.8219, -0.6060],
    "Orihuela": [38.0848, -0.9440],
    "Pego": [38.8428, -0.1172],
    "Polinyà de Xúquer": [39.1964, -0.3697],
    "Rojales": [38.0886, -0.7222],
    "Sagunt/Sagunto": [39.6797, -0.2783],
    "Segorbe": [39.8519, -0.4894],
    "Sollana": [39.2786, -0.3828],
    "Torreblanca": [40.2208, 0.1961],
    "Turís": [39.3894, -0.7103],
    "Utiel": [39.5667, -1.2044],
    "València": [39.4699, -0.3763],
    "Valencia Aeropuerto": [39.4893, -0.4816],
    "Villafranca del Cid/Vilafranca": [40.4272, -0.2578],
    "Villena": [38.6373, -0.8657],
    "Vinaròs-Viveros Alcanar": [40.5436, 0.4800],
    "Xàtiva": [38.9890, -0.5156],
    "Zarra": [39.0922, -1.0758],
}


def limpiar_numero(valor):
    if valor is None:
        return None

    texto = str(valor).strip().replace(",", ".")

    if texto == "":
        return None

    try:
        return float(texto)
    except ValueError:
        return None


def calcular_dpv(temp_c, hr):
    """
    DPV en kPa.
    es = presión de vapor de saturación
    ea = presión real de vapor
    """
    es = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    ea = es * (hr / 100.0)
    dpv = es - ea
    return dpv


def nivel_dpv(dpv):
    if dpv < 0.8:
        return "Bajo"
    elif dpv < 1.2:
        return "Moderado"
    elif dpv < 1.6:
        return "Alto"
    elif dpv < 2.0:
        return "Muy alto"
    else:
        return "Extremo"


def color_dpv(dpv):
    if dpv < 0.8:
        return "#2b83ba"
    elif dpv < 1.2:
        return "#abdda4"
    elif dpv < 1.6:
        return "#fdae61"
    elif dpv < 2.0:
        return "#f46d43"
    else:
        return "#d7191c"


def descargar_csv_aemet():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MetVlc-DPV-AEMET/1.0; +https://metvlc.blogspot.com)"
    }

    response = requests.get(URL_AEMET, headers=headers, timeout=120)
    response.raise_for_status()

    # AEMET suele entregar este CSV en latin1/cp1252.
    response.encoding = response.apparent_encoding or "latin1"

    return response.text


def extraer_fecha_hora(texto_csv):
    lineas = texto_csv.splitlines()

    actualizado = ""
    fecha_hora = ""

    for linea in lineas[:5]:
        if linea.startswith("Actualizado:"):
            actualizado = linea.replace("Actualizado:", "").strip()
        if linea.startswith("Fecha y hora:"):
            fecha_hora = linea.replace("Fecha y hora:", "").strip()

    return actualizado, fecha_hora


def leer_datos(texto_csv):
    lineas = texto_csv.splitlines()

    # El encabezado real empieza en la primera línea que contiene "Estación".
    idx_header = None
    for i, linea in enumerate(lineas):
        if '"Estación"' in linea or "Estación" in linea:
            idx_header = i
            break

    if idx_header is None:
        raise RuntimeError("No se ha encontrado la cabecera del CSV de AEMET.")

    contenido = "\n".join(lineas[idx_header:])
    f = io.StringIO(contenido)

    reader = csv.DictReader(f)

    filas = []
    for row in reader:
        filas.append(row)

    return filas


def main():
    print("Descargando CSV AEMET...")
    texto_csv = descargar_csv_aemet()

    actualizado_txt, fecha_hora_txt = extraer_fecha_hora(texto_csv)

    print("Actualizado AEMET:", actualizado_txt)
    print("Fecha y hora AEMET:", fecha_hora_txt)

    filas = leer_datos(texto_csv)

    features = []
    sin_coordenadas = []
    sin_datos = []

    for row in filas:
        estacion = row.get("Estación", "").strip()
        provincia = row.get("Provincia", "").strip()

        temp = limpiar_numero(row.get("Temperatura (ºC)"))
        hr = limpiar_numero(row.get("Humedad (%)"))

        if estacion not in COORDS:
            sin_coordenadas.append(estacion)
            continue

        if temp is None or hr is None:
            sin_datos.append(estacion)
            continue

        lat, lon = COORDS[estacion]
        dpv = calcular_dpv(temp, hr)

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": {
                "estacion": estacion,
                "provincia": provincia,
                "temperatura": round(temp, 1),
                "humedad": round(hr, 0),
                "dpv": round(dpv, 2),
                "nivel": nivel_dpv(dpv),
                "color": color_dpv(dpv)
            }
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    OUT_GEOJSON.write_text(
        json.dumps(geojson, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    metadata = {
        "producto": "DPV - Déficit de Presión de Vapor AEMET",
        "fuente": "AEMET últimos datos horarios",
        "url": URL_AEMET,
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "actualizado_aemet": actualizado_txt,
        "fecha_hora_aemet": fecha_hora_txt,
        "estaciones_csv": len(filas),
        "estaciones_con_datos": len(features),
        "estaciones_sin_datos": sin_datos,
        "estaciones_sin_coordenadas": sin_coordenadas,
        "formula": "DPV = es - ea; es = 0.6108 * exp((17.27*T)/(T+237.3)); ea = es * HR/100",
        "nota": "Coordenadas aproximadas/manuales asociadas a las estaciones AEMET."
    }

    OUT_METADATA.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Estaciones CSV: {len(filas)}")
    print(f"Estaciones con datos: {len(features)}")
    print(f"Sin datos: {len(sin_datos)}")
    print(f"Sin coordenadas: {len(sin_coordenadas)}")
    print(f"GeoJSON generado: {OUT_GEOJSON}")
    print(f"Metadata generado: {OUT_METADATA}")


if __name__ == "__main__":
    main()
