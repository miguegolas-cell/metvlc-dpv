from pathlib import Path
from datetime import datetime, timezone
import csv
import os
import time
from difflib import SequenceMatcher
from datetime import timedelta
import io
import json
import math
import unicodedata

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


URL_AEMET = "https://www.aemet.es/es/eltiempo/observacion/ultimosdatos_comunitat-valenciana_datos-horarios.csv?k=val&datos=det&w=0&f=temperatura&x=h24"

URL_OPENDATA = "https://opendata.aemet.es/opendata/api/observacion/convencional/todas"
CSV_ALTERNATIVAS = [
    URL_AEMET,
    "https://www.aemet.es/es/eltiempo/observacion/ultimosdatos_comunitat-valenciana_datos-horarios.csv?k=val&datos=det&w=0",
    "https://www.aemet.es/es/eltiempo/observacion/ultimosdatos_comunitat-valenciana_datos-horarios.csv",
]

DOCS_DIR = Path("docs")
DOCS_DIR.mkdir(exist_ok=True)

OUT_GEOJSON = DOCS_DIR / "dpv_aemet.geojson"
OUT_METADATA = DOCS_DIR / "metadata_aemet.json"


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


def normalizar_texto(texto):
    texto = str(texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace("º", "").replace("°", "")
    texto = texto.replace("(", " ").replace(")", " ")
    texto = " ".join(texto.split())
    return texto


def buscar_columna(row, posibles):
    columnas = list(row.keys())

    columnas_norm = {
        normalizar_texto(col): col
        for col in columnas
    }

    for posible in posibles:
        posible_norm = normalizar_texto(posible)

        for col_norm, col_original in columnas_norm.items():
            if posible_norm in col_norm:
                return col_original

    return None


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
    es = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    ea = es * (hr / 100.0)
    return es - ea


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




# ============================================================
# Descarga y selección de observaciones AEMET
# ============================================================

CABECERAS = {
    "User-Agent": "Mozilla/5.0 (compatible; MetVlc-DPV/2.0; +https://metvlc.blogspot.com)",
    "Accept": "application/json,text/csv,text/plain,*/*",
}


def sesion_http():
    sesion = requests.Session()
    reintentos = Retry(
        total=3, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
    )
    adaptador = HTTPAdapter(max_retries=reintentos)
    sesion.mount("https://", adaptador)
    sesion.mount("http://", adaptador)
    sesion.headers.update(CABECERAS)
    return sesion


def descargar_datos_opendata(api_key, sesion):
    """Consulta endpoint oficial; la URL `datos` de la primera respuesta es temporal."""
    respuesta = sesion.get(URL_OPENDATA, headers={"api_key": api_key}, timeout=60)
    respuesta.raise_for_status()
    descriptor = respuesta.json()
    url_datos = descriptor.get("datos")
    if not isinstance(url_datos, str) or not url_datos.startswith("https://"):
        raise RuntimeError(f"AEMET OpenData no devolvió URL de datos válida: {descriptor}")
    respuesta_datos = sesion.get(url_datos, timeout=90)
    respuesta_datos.raise_for_status()
    datos = respuesta_datos.json()
    if not isinstance(datos, list) or not datos:
        raise RuntimeError("AEMET OpenData no devolvió observaciones en formato lista.")
    return datos


def parsear_fecha_api(valor):
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def distancia_km(lat1, lon1, lat2, lon2):
    r = 6371.009
    a1, a2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lat2 - lat1)
    dn = math.radians(lon2 - lon1)
    a = math.sin(dl / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(dn / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def seleccionar_estaciones_api(datos, ahora=None):
    """Reutiliza las 43 estaciones configuradas para preservar la integración.

    Cada punto de AEMET puede contener varias horas. Se usa la observación
    válida más reciente por idema, sin sustituir una estación por otra cercana.
    """
    ahora = ahora or datetime.now(timezone.utc)
    max_edad = timedelta(hours=18)
    recientes = {}
    for obs in datos:
        if not isinstance(obs, dict):
            continue
        ident = str(obs.get("idema") or "").strip()
        fecha = parsear_fecha_api(obs.get("fint"))
        temp = limpiar_numero(obs.get("ta"))
        hr = limpiar_numero(obs.get("hr"))
        lat = limpiar_numero(obs.get("lat"))
        lon = limpiar_numero(obs.get("lon"))
        if not ident or fecha is None or None in (temp, hr, lat, lon):
            continue
        if fecha > ahora + timedelta(minutes=10) or ahora - fecha > max_edad:
            continue
        if not (-45 <= temp <= 55 and 0 <= hr <= 100):
            continue
        # Preselección espacial; la asignación fina usa las coordenadas de COORDS.
        if not (37.5 <= lat <= 41.1 and -1.9 <= lon <= 1.0):
            continue
        if ident not in recientes or fecha > recientes[ident]["_fecha"]:
            recientes[ident] = {**obs, "_fecha": fecha, "_temp": temp, "_hr": hr,
                                "_lat": lat, "_lon": lon, "_id": ident}

    # Coincidencias de nombre y proximidad. Asignación uno a uno por idema.
    candidatos = []
    for nombre, (lat_t, lon_t) in COORDS.items():
        nombre_norm = normalizar_texto(nombre).replace("/", " ").replace("-", " ")
        for ident, obs in recientes.items():
            dist = distancia_km(lat_t, lon_t, obs["_lat"], obs["_lon"])
            if dist > 15:
                continue
            estacion_norm = normalizar_texto(obs.get("ubi", "")).replace("/", " ").replace("-", " ")
            similitud = SequenceMatcher(None, nombre_norm, estacion_norm).ratio()
            # Impedir asignar una estación diferente únicamente porque está cerca.
            if similitud < 0.43 and dist > 3.0:
                continue
            puntuacion = dist - 12 * similitud
            if nombre_norm == estacion_norm:
                puntuacion -= 50
            candidatos.append((puntuacion, dist, nombre, ident))

    candidatos.sort()
    elegidos, usados = {}, set()
    for _, dist, nombre, ident in candidatos:
        if nombre not in elegidos and ident not in usados:
            elegidos[nombre] = recientes[ident]
            usados.add(ident)

    features = []
    for nombre, obs in elegidos.items():
        temp, hr = obs["_temp"], obs["_hr"]
        dpv = calcular_dpv(temp, hr)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [obs["_lon"], obs["_lat"]]},
            "properties": {
                "estacion": nombre,
                "provincia": "",
                "temperatura": round(temp, 1),
                "humedad": round(hr, 0),
                "dpv": round(dpv, 2),
                "nivel": nivel_dpv(dpv),
                "color": color_dpv(dpv),
                "fuente": "AEMET",
                "idema": obs["_id"],
                "fecha_observacion_utc": obs["_fecha"].isoformat(),
                "estacion_aemet": str(obs.get("ubi") or nombre),
            },
        })
    features.sort(key=lambda f: f["properties"]["estacion"])
    sin_coordenadas = sorted(set(COORDS) - set(elegidos))
    ultima = max((obs["_fecha"] for obs in elegidos.values()), default=None)
    info = {
        "estaciones_csv": len(recientes),
        "estaciones_con_datos": len(features),
        "estaciones_sin_datos": [],
        "estaciones_sin_coordenadas": sin_coordenadas,
        "columnas_detectadas": ["idema", "ubi", "fint", "lat", "lon", "ta", "hr"],
        "columna_temperatura_usada": "ta",
        "columna_humedad_usada": "hr",
        "fecha_hora_aemet": ultima.isoformat() if ultima else "",
        "actualizado_aemet": ultima.isoformat() if ultima else "",
        "fuente_tecnica": "AEMET OpenData / observacion/convencional/todas",
        "url": URL_OPENDATA,
        "observaciones_recientes_con_temp_y_hr": len(recientes),
    }
    return features, info


def descargar_csv_aemet(sesion):
    errores = []
    for url in CSV_ALTERNATIVAS:
        try:
            print(f"Probando CSV público: {url}")
            respuesta = sesion.get(url, timeout=45)
            respuesta.raise_for_status()
            respuesta.encoding = "latin-1"
            texto = respuesta.text
            # Evitar guardar páginas HTML de error como si fueran datos.
            if "<html" in texto[:500].lower() or "<!doctype" in texto[:500].lower():
                raise ValueError("AEMET devolvió HTML en lugar de CSV")
            if "Estación" not in texto and "Estacion" not in texto:
                raise ValueError("No se encontró la cabecera de estaciones")
            return texto, url
        except (requests.RequestException, ValueError) as error:
            errores.append(f"{url}: {error}")
    raise RuntimeError("No se pudo descargar el CSV de AEMET:\n" + "\n".join(errores))


def extraer_fecha_hora(texto_csv):
    actualizado, fecha_hora = "", ""
    for linea in texto_csv.splitlines()[:12]:
        linea = linea.strip().strip('"')
        if linea.startswith("Actualizado:"):
            actualizado = linea.partition(":")[2].strip()
        if linea.startswith("Fecha y hora:"):
            fecha_hora = linea.partition(":")[2].strip()
    return actualizado, fecha_hora


def leer_datos(texto_csv):
    lineas = texto_csv.replace("\ufeff", "").splitlines()
    idx_header = next((i for i, lin in enumerate(lineas) if "Estación" in lin or "Estacion" in lin), None)
    if idx_header is None:
        raise RuntimeError("No se encontró cabecera en CSV de AEMET")
    muestra = "\n".join(lineas[idx_header:idx_header+6])
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
        separador = dialecto.delimiter
    except csv.Error:
        separador = ","
    reader = csv.DictReader(io.StringIO("\n".join(lineas[idx_header:])), delimiter=separador)
    filas = list(reader)
    if not filas or not reader.fieldnames:
        raise RuntimeError("CSV sin filas o columnas")
    print("Columnas CSV:", reader.fieldnames)
    return filas, reader.fieldnames


def seleccionar_estaciones_csv(texto):
    filas, columnas = leer_datos(texto)
    primera = filas[0]
    col_estacion = buscar_columna(primera, ["estacion"])
    col_provincia = buscar_columna(primera, ["provincia"])
    col_temp = buscar_columna(primera, ["temperatura", "temp.", "temp"])
    col_hr = buscar_columna(primera, ["humedad", "hum.", "hum "])
    if not all([col_estacion, col_temp, col_hr]):
        raise RuntimeError(f"Columnas CSV incompletas: {columnas}")
    noms_norm = {normalizar_texto(n): n for n in COORDS}
    features, sin_coordenadas, sin_datos = [], [], []
    for row in filas:
        estacion_csv = str(row.get(col_estacion) or "").strip()
        if not estacion_csv:
            continue
        nombre = noms_norm.get(normalizar_texto(estacion_csv))
        if nombre is None:
            sin_coordenadas.append(estacion_csv)
            continue
        temp = limpiar_numero(row.get(col_temp))
        hr = limpiar_numero(row.get(col_hr))
        if temp is None or hr is None or not (-45 <= temp <= 55 and 0 <= hr <= 100):
            sin_datos.append(estacion_csv)
            continue
        lat, lon = COORDS[nombre]
        dpv = calcular_dpv(temp, hr)
        features.append({"type":"Feature", "geometry":{"type":"Point","coordinates":[lon,lat]},
                         "properties":{"estacion":nombre, "provincia":str(row.get(col_provincia) or "") if col_provincia else "",
                                       "temperatura":round(temp,1), "humedad":round(hr), "dpv":round(dpv,2),
                                       "nivel":nivel_dpv(dpv), "color":color_dpv(dpv), "fuente":"AEMET"}})
    actualizado, fecha_hora = extraer_fecha_hora(texto)
    return features, {
        "estaciones_csv": len(filas), "estaciones_con_datos": len(features),
        "estaciones_sin_datos": sin_datos,
        "estaciones_sin_coordenadas": sin_coordenadas,
        "columnas_detectadas": columnas,
        "columna_temperatura_usada": col_temp,
        "columna_humedad_usada": col_hr,
        "actualizado_aemet": actualizado,
        "fecha_hora_aemet": fecha_hora,
        "fuente_tecnica": "CSV público AEMET",
    }


def guardar_json_atomico(ruta, datos):
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    temporal.replace(ruta)


def main():
    sesion = sesion_http()
    api_key = os.environ.get("AEMET_API_KEY", "").strip()
    errores = []
    features = []
    info = {}

    if api_key:
        try:
            print("Consultando AEMET OpenData (fuente prioritaria)...")
            datos = descargar_datos_opendata(api_key, sesion)
            features, info = seleccionar_estaciones_api(datos)
            print(f"OpenData: {len(features)} estaciones válidas de {len(COORDS)} previstas")
        except Exception as error:
            errores.append(f"OpenData: {error}")
            print(f"::warning::AEMET OpenData falló: {error}")
    else:
        print("::warning::AEMET_API_KEY no configurada; intentando CSV público.")

    min_estaciones = max(1, int(os.getenv("AEMET_MIN_ESTACIONES", "12")))
    if len(features) < min_estaciones:
        if features:
            errores.append(f"OpenData devolvió solo {len(features)} estaciones, mínimo {min_estaciones}")
        try:
            texto, url_csv = descargar_csv_aemet(sesion)
            features_csv, info_csv = seleccionar_estaciones_csv(texto)
            info_csv["url"] = url_csv
            if len(features_csv) >= min_estaciones:
                features, info = features_csv, info_csv
            else:
                errores.append(f"CSV: {len(features_csv)} estaciones, mínimo {min_estaciones}")
        except Exception as error:
            errores.append(f"CSV: {error}")
            print(f"::warning::CSV AEMET falló: {error}")

    if len(features) < min_estaciones:
        raise RuntimeError(
            f"AEMET sin datos suficientes ({len(features)}/{min_estaciones}). "
            "No se sobrescriben los GeoJSON anteriores. "
            "Revisa la clave AEMET_API_KEY en Secrets > Actions.\n" + "\n".join(errores)
        )

    metadata = {
        "producto": "DPV - Déficit de Presión de Vapor AEMET",
        "fuente": "AEMET últimos datos horarios",
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        **info,
        "formula": "DPV = es - ea; es = 0.6108 * exp((17.27*T)/(T+237.3)); ea = es * HR/100",
        "nota": "En OpenData, coordenadas oficiales reportadas por AEMET; correspondencia con estaciones originales por nombre y proximidad.",
    }
    geojson = {"type": "FeatureCollection", "features": features}
    guardar_json_atomico(OUT_GEOJSON, geojson)
    guardar_json_atomico(OUT_METADATA, metadata)
    print(f"OK AEMET: {len(features)} estaciones | origen={info.get('fuente_tecnica')}")
    print(f"GeoJSON: {OUT_GEOJSON} | Metadata: {OUT_METADATA}")


if __name__ == "__main__":
    main()
