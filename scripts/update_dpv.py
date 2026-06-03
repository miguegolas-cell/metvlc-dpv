from pathlib import Path
from datetime import datetime, timezone
import csv
import json
import math
import os
import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup


URL_AVAMET = "https://www.avamet.org/mxo-mxo.php?territori=p46"

DOCS_DIR = Path("docs")
ESTACIONES_CSV = DOCS_DIR / "estaciones.csv"
DPV_GEOJSON = DOCS_DIR / "dpv.geojson"
METADATA_JSON = DOCS_DIR / "metadata.json"


class AvametBloqueoError(Exception):
    """Error usado cuando AVAMET responde con bloqueo o rate limit."""
    pass


def normalizar(texto):
    if texto is None:
        return ""

    texto = str(texto).strip().lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.replace("′", "'")
    texto = re.sub(r"[^a-z0-9]+", "", texto)
    return texto


def limpiar_numero(valor):
    if valor is None:
        return None

    texto = str(valor).strip()
    texto = texto.replace(",", ".")
    texto = texto.replace("−", "-")

    if texto in ["", "-", "--"]:
        return None

    if texto.lower() in ["nan", "none"]:
        return None

    match = re.search(r"[-+]?\d+(?:\.\d+)?", texto)
    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def calcular_dpv(temp_c, hr):
    """
    Calcula el DPV en kPa.

    Fórmula:
    es = 0.6108 * exp((17.27*T)/(T+237.3))
    ea = es * HR/100
    DPV = es - ea
    """
    es = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    ea = es * (hr / 100.0)
    return es - ea


def clasificar_dpv(dpv):
    if dpv < 1.0:
        return "Bajo", "#2b83ba"
    elif dpv < 1.5:
        return "Moderado", "#fdae61"
    elif dpv < 2.5:
        return "Alto", "#f46d43"
    else:
        return "Muy alto", "#d7191c"


def cargar_estaciones_base():
    estaciones = []

    with ESTACIONES_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            estacion = row.get("estacion", "").strip()

            if not estacion:
                continue

            estaciones.append({
                "municipio": row.get("municipio", "").strip(),
                "estacion": estacion,
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "clave": normalizar(estacion),
            })

    return estaciones


def descargar_html_avamet():
    """
    Descarga la página de AVAMET de forma prudente.

    - Hace como máximo 3 intentos.
    - Usa un User-Agent identificable.
    - No insiste si AVAMET devuelve 403 o 429.
    - Evita timeouts excesivamente largos.
    """

    user_agent = os.getenv(
        "AVAMET_USER_AGENT",
        "MetVlc-DPV/1.0 uso divulgativo no comercial - Fuente AVAMET - https://metvlc.blogspot.com"
    )

    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ca,es;q=0.9,en;q=0.8",
        "Connection": "close",
    }

    errores = []

    for intento in range(1, 4):
        try:
            print(f"Intento {intento}/3 descargando AVAMET...")
            print(f"URL: {URL_AVAMET}")

            response = requests.get(
                URL_AVAMET,
                headers=headers,
                timeout=(10, 30)
            )

            print(f"Código HTTP AVAMET: {response.status_code}")

            if response.status_code in (403, 429):
                raise AvametBloqueoError(
                    f"AVAMET ha rechazado la petición con código {response.status_code}. "
                    "Se detiene el proceso para no insistir sobre el servidor."
                )

            response.raise_for_status()

            if not response.text or len(response.text) < 1000:
                raise RuntimeError("Respuesta de AVAMET demasiado corta o vacía.")

            print("Descarga AVAMET correcta.")
            return response.text

        except AvametBloqueoError as e:
            error = f"Bloqueo/rate limit detectado: {repr(e)}"
            print(error)
            errores.append(error)
            break

        except Exception as e:
            error = f"Intento {intento} fallido: {repr(e)}"
            print(error)
            errores.append(error)

            if intento < 3:
                espera = intento * 30
                print(f"Esperando {espera} segundos antes de reintentar...")
                time.sleep(espera)

    raise RuntimeError(
        "No se pudo descargar AVAMET. "
        + " | ".join(errores)
    )


def extraer_filas_desde_html(html):
    soup = BeautifulSoup(html, "lxml")

    registros = {}

    for tr in soup.find_all("tr"):
        celdas = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]

        if len(celdas) < 6:
            continue

        nombre = celdas[0].strip()

        if not nombre:
            continue

        if nombre.lower() in ["estación", "estacio", "estació"]:
            continue

        clave = normalizar(nombre)

        numeros = []
        for celda in celdas[1:]:
            n = limpiar_numero(celda)
            if n is not None:
                numeros.append(n)

        if len(numeros) < 6:
            continue

        # Estructura aproximada:
        # altitud, tendencia/opcional, temperatura, mínima, máxima,
        # punto de rocío, humedad...
        datos = numeros[1:]

        # Saltar posible tendencia justo después de altitud.
        if datos and abs(datos[0]) <= 60 and float(datos[0]).is_integer() and len(datos) >= 7:
            datos = datos[1:]

        if len(datos) < 5:
            continue

        temp = datos[0]
        hr = datos[4]

        if temp < -30 or temp > 55:
            continue

        if hr < 1 or hr > 100:
            posibles_hr = [x for x in datos[1:] if 1 <= x <= 100]
            if posibles_hr:
                hr = posibles_hr[-1]
            else:
                continue

        registros[clave] = {
            "nombre_avamet": nombre,
            "temperatura": temp,
            "humedad": hr,
        }

    return registros


def buscar_registro(estacion_base, registros):
    clave = estacion_base["clave"]

    if clave in registros:
        return registros[clave]

    for k, v in registros.items():
        if clave in k or k in clave:
            return v

    return None


def generar_geojson(estaciones_base, registros_avamet):
    features = []
    sin_datos = []

    for est in estaciones_base:
        reg = buscar_registro(est, registros_avamet)

        if not reg:
            sin_datos.append(est["estacion"])
            continue

        temp = reg["temperatura"]
        hr = reg["humedad"]
        dpv = calcular_dpv(temp, hr)
        nivel, color = clasificar_dpv(dpv)

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [est["lon"], est["lat"]]
            },
            "properties": {
                "municipio": est["municipio"],
                "estacion": est["estacion"],
                "nombre_avamet": reg["nombre_avamet"],
                "temperatura": round(temp, 1),
                "humedad": round(hr, 0),
                "dpv": round(dpv, 2),
                "nivel": nivel,
                "color": color,
                "fuente": "AVAMET",
                "fuente_detalle": "AVAMET - Associació Valenciana de Meteorologia",
                "uso": "Divulgativo no comercial",
            }
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    return geojson, sin_datos


def guardar_json(path, contenido):
    """
    Guarda JSON de forma segura.
    Primero escribe un archivo temporal y luego reemplaza el definitivo.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    tmp_path.write_text(
        json.dumps(contenido, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    tmp_path.replace(path)


def main():
    print("Cargando estaciones base...")
    estaciones_base = cargar_estaciones_base()
    print(f"Estaciones base: {len(estaciones_base)}")

    print("Descargando AVAMET...")
    html = descargar_html_avamet()

    print("Extrayendo datos AVAMET...")
    registros = extraer_filas_desde_html(html)
    print(f"Registros AVAMET detectados: {len(registros)}")

    print("Generando DPV GeoJSON...")
    geojson, sin_datos = generar_geojson(estaciones_base, registros)

    metadata = {
        "producto": "DPV - Déficit de Presión de Vapor",
        "fuente": "AVAMET MXO MeteoXarxaOnline",
        "fuente_entidad": "AVAMET - Associació Valenciana de Meteorologia",
        "url": URL_AVAMET,
        "uso": "Divulgativo no comercial",
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "estaciones_base": len(estaciones_base),
        "estaciones_con_datos": len(geojson["features"]),
        "estaciones_sin_datos": sin_datos,
        "formula": "DPV = es - ea; es = 0.6108 * exp((17.27*T)/(T+237.3)); ea = es * HR/100",
        "nota": "Cálculo y visualización realizados por MetVLC a partir de datos publicados por AVAMET."
    }

    print("Guardando archivos...")
    guardar_json(DPV_GEOJSON, geojson)
    guardar_json(METADATA_JSON, metadata)

    print(f"Estaciones con datos: {len(geojson['features'])}")
    print(f"Estaciones sin datos: {len(sin_datos)}")

    if sin_datos:
        print("Sin datos:")
        for s in sin_datos:
            print(" -", s)

    print("Archivos generados:")
    print(f" - {DPV_GEOJSON}")
    print(f" - {METADATA_JSON}")


if __name__ == "__main__":
    main()
