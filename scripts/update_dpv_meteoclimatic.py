from pathlib import Path
from datetime import datetime, timezone
import json
import math
import re
import xml.etree.ElementTree as ET

import requests


# RSS Meteoclimatic provincia de Valencia
URLS_METEOCLIMATIC = [
    "https://www.meteoclimatic.net/feed/rss/ESPVA46000000",
    "http://www.meteoclimatic.net/feed/rss/ESPVA46000000",
    "https://www2.meteoclimatic.net/feed/rss/ESPVA46000000",
    "http://www2.meteoclimatic.net/feed/rss/ESPVA46000000",
]

DOCS_DIR = Path("docs")
DOCS_DIR.mkdir(exist_ok=True)

OUT_GEOJSON = DOCS_DIR / "dpv_meteoclimatic.geojson"
OUT_METADATA = DOCS_DIR / "metadata_meteoclimatic.json"


# Selección filtrada: representación equilibrada de la provincia de València
ESTACIONES_SELECCIONADAS = [
    "Torrebaja",
    "casinos",
    "Pedralba - Serretilla",
    "Llíria",
    "Vilamarxant - Aj.",
    "Cheste",
    "Buñol - C.Cid",
    "Chiva - Cumbres Calicanto",
    "Turís - Greenval",

    "Faura",
    "Gilet",
    "Sagunt - Port de Sagunt",
    "El Puig de Santa Maria",
    "Bétera - Centro",

    "Valencia - Caravaca",
    "Xirivella",
    "Torrent - Av. Al Vedat",
    "Albal",

    "El Perelló",
    "Sueca",
    "Cullera - Dosser",
    "Albalat de la Ribera",
    "Carcaixent - Muntanyeta",
    "Alzira - La Casella",
    "Benimodo",
    "Alberic - Nord",
    "Massalavés",
    "Tous",
    "Quesa - EcoCampingQuesa",
    "Anna",

    "Castelló",
    "Xàtiva - Ajuntament",
    "Barxeta",
    "Montesa",
    "Canals",

    "Tavernes de la Valldigna",
    "Xeresa",
    "Gandia",
    "Oliva Nova",
    "Vilallonga/Villalonga-Aj.",

    "Ontinyent - Alba",
    "Albaida",
    "Benigànim",
    "L'Olleria - Ajuntament",
    "La Pobla del Duc",
    "Quatretonda",
    "Almiserà",

    "Moixent - Cumbres",
    "Fontanars dels Alforins",
    "Font de la F. - Carrascal",
]


def normalizar_nombre(nombre):
    """
    Limpia el nombre de la estación:
    - quita la provincia entre paréntesis
    - elimina espacios duplicados
    """
    if not nombre:
        return ""

    nombre = re.sub(r"\s*\(Valencia\)\s*$", "", nombre)
    nombre = re.sub(r"\s+", " ", nombre)
    return nombre.strip()


def limpiar_numero(valor):
    if valor is None:
        return None

    texto = str(valor).strip()
    texto = texto.replace(",", ".")

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
    return es - ea


def nivel_dpv(dpv):
    if dpv < 1.0:
        return "Bajo"
    elif dpv < 1.5:
        return "Moderado"
    elif dpv < 2.5:
        return "Alto"
    else:
        return "Muy alto"


def color_dpv(dpv):
    if dpv < 1.0:
        return "#2b83ba"
    elif dpv < 1.5:
        return "#fdae61"
    elif dpv < 2.5:
        return "#f46d43"
    else:
        return "#d7191c"


def descargar_rss_meteoclimatic():
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MetVlc-DPV-Meteoclimatic/1.0; +https://metvlc.blogspot.com)"
    }

    errores = []

    for url in URLS_METEOCLIMATIC:
        try:
            print(f"Probando descarga: {url}")
            response = requests.get(url, headers=headers, timeout=120)
            response.raise_for_status()

            if not response.content or len(response.content) < 1000:
                raise RuntimeError("Respuesta demasiado corta.")

            print("Descarga correcta:", url)
            return response.content, url

        except Exception as e:
            error = f"{url} -> {repr(e)}"
            print("Fallo:", error)
            errores.append(error)

    raise RuntimeError(
        "No se pudo descargar el RSS de Meteoclimatic. "
        + " | ".join(errores)
    )


def extraer_temp_humedad(descripcion):
    """
    Extrae temperatura y humedad desde el HTML incluido en description.
    Ejemplo:
    Temperatura: <b>25,0</b>
    Humedad: <b>77,0</b>
    """
    temp = None
    humedad = None

    m_temp = re.search(r"Temperatura:\s*<b>([^<]+)</b>", descripcion)
    if m_temp:
        temp = limpiar_numero(m_temp.group(1))

    m_hr = re.search(r"Humedad:\s*<b>([^<]+)</b>", descripcion)
    if m_hr:
        humedad = limpiar_numero(m_hr.group(1))

    return temp, humedad


def extraer_actualizado(descripcion):
    m = re.search(r"Actualizado:\s*([^<]+)</li>", descripcion)
    if m:
        return m.group(1).strip()
    return ""


def extraer_coordenadas(item):
    """
    Primero intenta georss:point.
    Si no está, intenta geo:lat / geo:long.
    """
    ns_georss = "{http://www.georss.org/georss}"
    ns_geo = "{http://www.w3.org/2003/01/geo/wgs84_pos#}"

    point = item.findtext(f"{ns_georss}point")
    if point:
        partes = point.strip().split()
        if len(partes) == 2:
            lat = limpiar_numero(partes[0])
            lon = limpiar_numero(partes[1])
            if lat is not None and lon is not None:
                return lat, lon

    lat = item.findtext(f".//{ns_geo}lat")
    lon = item.findtext(f".//{ns_geo}long")

    lat = limpiar_numero(lat)
    lon = limpiar_numero(lon)

    if lat is not None and lon is not None:
        return lat, lon

    return None, None


def main():
    print("Descargando RSS Meteoclimatic...")
    contenido, url_usada = descargar_rss_meteoclimatic()

    root = ET.fromstring(contenido)
    channel = root.find("channel")

    if channel is None:
        raise RuntimeError("No se ha encontrado el canal RSS.")

    pub_date = channel.findtext("pubDate") or ""

    seleccion = set(ESTACIONES_SELECCIONADAS)

    total_items = 0
    features = []

    sin_datos = []
    sin_coordenadas = []
    no_seleccionadas = []

    for item in channel.findall("item"):
        total_items += 1

        titulo = item.findtext("title") or ""
        estacion = normalizar_nombre(titulo)

        if estacion not in seleccion:
            no_seleccionadas.append(estacion)
            continue

        descripcion = item.findtext("description") or ""
        link = item.findtext("link") or ""
        pub_item = item.findtext("pubDate") or ""

        temp, hr = extraer_temp_humedad(descripcion)
        actualizado = extraer_actualizado(descripcion)
        lat, lon = extraer_coordenadas(item)

        if lat is None or lon is None:
            sin_coordenadas.append(estacion)
            continue

        if temp is None or hr is None:
            sin_datos.append(estacion)
            continue

        dpv = calcular_dpv(temp, hr)

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": {
                "estacion": estacion,
                "temperatura": round(temp, 1),
                "humedad": round(hr, 0),
                "dpv": round(dpv, 2),
                "nivel": nivel_dpv(dpv),
                "color": color_dpv(dpv),
                "actualizado": actualizado,
                "pubDate": pub_item,
                "fuente": "Meteoclimatic",
                "url": link
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
        "producto": "DPV - Déficit de Presión de Vapor Meteoclimatic",
        "fuente": "Meteoclimatic RSS",
        "url": url_usada,
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "rss_pubDate": pub_date,
        "estaciones_rss": total_items,
        "estaciones_seleccionadas": len(ESTACIONES_SELECCIONADAS),
        "estaciones_con_datos": len(features),
        "estaciones_sin_datos": sin_datos,
        "estaciones_sin_coordenadas": sin_coordenadas,
        "formula": "DPV = es - ea; es = 0.6108 * exp((17.27*T)/(T+237.3)); ea = es * HR/100",
        "nota": "Producto no oficial. Red colaborativa Meteoclimatic. Se usa una selección filtrada de estaciones para evitar saturación visual."
    }

    OUT_METADATA.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("Total estaciones RSS:", total_items)
    print("Estaciones seleccionadas:", len(ESTACIONES_SELECCIONADAS))
    print("Estaciones con datos:", len(features))
    print("Sin datos:", len(sin_datos))
    print("Sin coordenadas:", len(sin_coordenadas))
    print("GeoJSON generado:", OUT_GEOJSON)
    print("Metadata generado:", OUT_METADATA)


if __name__ == "__main__":
    main()
