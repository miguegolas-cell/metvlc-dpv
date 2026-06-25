from pathlib import Path
from datetime import datetime, timezone
import csv
import json
import math

BASE_DIR = Path('.')
DATA_DIR = BASE_DIR / 'data'
DOCS_DIR = BASE_DIR / 'docs'
DOCS_DIR.mkdir(exist_ok=True)

ESTACIONES_CSV = DATA_DIR / 'estaciones_dpv_integrado.csv'
OUT_GEOJSON = DOCS_DIR / 'dpv_integrado.geojson'
OUT_METADATA = DOCS_DIR / 'metadata_integrado.json'

# Si una fuente no se ha generado en este margen, se considera no actualizada.
# Para DPV operativo con varias actualizaciones diarias, 12 h es prudente.
MAX_EDAD_HORAS = 12

FUENTES = {
    'AEMET': {
        'geojson': DOCS_DIR / 'dpv_aemet.geojson',
        'metadata': DOCS_DIR / 'metadata_aemet.json',
    },
    'AVAMET': {
        'geojson': DOCS_DIR / 'dpv.geojson',
        'metadata': DOCS_DIR / 'metadata.json',
    },
    'Meteoclimatic': {
        'geojson': DOCS_DIR / 'dpv_meteoclimatic.geojson',
        'metadata': DOCS_DIR / 'metadata_meteoclimatic.json',
    },
}


def limpiar_float(valor):
    if valor is None:
        return None
    texto = str(valor).strip().replace(',', '.')
    if texto == '' or texto.lower() in {'none', 'null', 'nan'}:
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
    if dpv is None:
        return "Sin datos"

    if dpv < 0.5:
        return "Aire muy húmedo"
    elif dpv < 1.0:
        return "Condiciones favorables"
    elif dpv < 2.0:
        return "Equilibrio hídrico"
    elif dpv < 3.0:
        return "Ambiente seco"
    elif dpv < 4.0:
        return "Alto estrés hídrico"
    else:
        return "Extremo estrés hídrico"


def color_dpv(dpv):
    if dpv is None:
        return "#9e9e9e"

    if dpv < 0.5:
        return "#2166ac"   # Azul oscuro
    elif dpv < 1.0:
        return "#67a9cf"   # Azul claro
    elif dpv < 2.0:
        return "#1a9850"   # Verde
    elif dpv < 3.0:
        return "#fdae61"   # Naranja
    elif dpv < 4.0:
        return "#f46d43"   # Rojo anaranjado
    else:
        return "#a50026"   # Rojo oscuro


def normalizar_clave(texto):
    return str(texto or '').strip().lower()


def parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None


def fuente_actualizada(metadata):
    generado = metadata.get('generado_utc') if isinstance(metadata, dict) else None
    dt = parse_utc(generado)
    if dt is None:
        return False, None, None
    ahora = datetime.now(timezone.utc)
    edad_horas = (ahora - dt).total_seconds() / 3600
    return edad_horas <= MAX_EDAD_HORAS, generado, round(edad_horas, 2)


def leer_metadata(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def leer_geojson_fuente(path):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}

    salida = {}
    for feature in data.get('features', []):
        props = feature.get('properties', {}) or {}
        estacion = props.get('estacion') or props.get('nombre_avamet') or props.get('nombre') or ''
        fuente = props.get('fuente') or ''
        if not estacion:
            continue
        salida[normalizar_clave(estacion)] = feature

        # Para AVAMET, a veces interesa poder cruzar por nombre_avamet.
        if props.get('nombre_avamet'):
            salida[normalizar_clave(props.get('nombre_avamet'))] = feature

    return salida


def leer_estaciones_base():
    if not ESTACIONES_CSV.exists():
        raise RuntimeError(f'No existe el listado base: {ESTACIONES_CSV}')

    estaciones = []
    with ESTACIONES_CSV.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            fuente = (row.get('fuente') or '').strip()
            estacion = (row.get('estacion') or '').strip()
            nombre = (row.get('nombre') or estacion).strip()
            municipio = (row.get('municipio') or '').strip()
            lat = limpiar_float(row.get('lat'))
            lon = limpiar_float(row.get('lon'))

            if not fuente or not estacion or lat is None or lon is None:
                continue

            estaciones.append({
                'fuente': fuente,
                'estacion': estacion,
                'nombre': nombre,
                'municipio': municipio,
                'lat': lat,
                'lon': lon,
            })
    return estaciones


def main():
    print('Generando DPV integrado MetVlc...')

    estaciones_base = leer_estaciones_base()
    print('Estaciones base:', len(estaciones_base))

    datos_por_fuente = {}
    estado_fuentes = {}

    for fuente, cfg in FUENTES.items():
        metadata = leer_metadata(cfg['metadata'])
        actualizada, generado_utc, edad_horas = fuente_actualizada(metadata)
        datos = leer_geojson_fuente(cfg['geojson']) if actualizada else {}

        datos_por_fuente[fuente] = datos
        estado_fuentes[fuente] = {
            'actualizada': actualizada,
            'generado_utc': generado_utc,
            'edad_horas': edad_horas,
            'features_disponibles': len(datos),
        }

        print(f'{fuente}: actualizada={actualizada} features={len(datos)} edad_horas={edad_horas}')

    features = []
    resumen = {
        'total': 0,
        'con_datos': 0,
        'sin_datos': 0,
        'por_fuente': {},
    }

    for est in estaciones_base:
        fuente = est['fuente']
        clave = normalizar_clave(est['estacion'])
        datos_fuente = datos_por_fuente.get(fuente, {})
        feature_origen = datos_fuente.get(clave)

        temp = None
        hr = None
        dpv = None
        actualizado = ''
        url = ''
        estado = 'sin datos'

        if feature_origen:
            props_origen = feature_origen.get('properties', {}) or {}
            temp = limpiar_float(props_origen.get('temperatura'))
            hr = limpiar_float(props_origen.get('humedad'))
            dpv = limpiar_float(props_origen.get('dpv'))
            actualizado = props_origen.get('actualizado') or props_origen.get('pubDate') or ''
            url = props_origen.get('url') or ''

            if temp is not None and hr is not None:
                if dpv is None:
                    dpv = calcular_dpv(temp, hr)
                estado = 'con datos'

        nivel = nivel_dpv(dpv) if estado == 'con datos' else 'Sin datos'
        color = color_dpv(dpv) if estado == 'con datos' else '#9aa3aa'

        props = {
            'fuente': fuente,
            'estacion': est['estacion'],
            'nombre': est['nombre'],
            'municipio': est['municipio'],
            'estado': estado,
            'temperatura': round(temp, 1) if temp is not None else None,
            'humedad': round(hr, 0) if hr is not None else None,
            'dpv': round(dpv, 2) if dpv is not None else None,
            'nivel': nivel,
            'color': color,
            'actualizado': actualizado,
            'url': url,
        }

        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [est['lon'], est['lat']],
            },
            'properties': props,
        })

        resumen['total'] += 1
        resumen[estado.replace(' ', '_')] += 1
        resumen['por_fuente'].setdefault(fuente, {'total': 0, 'con_datos': 0, 'sin_datos': 0})
        resumen['por_fuente'][fuente]['total'] += 1
        resumen['por_fuente'][fuente][estado.replace(' ', '_')] += 1

    geojson = {
        'type': 'FeatureCollection',
        'features': features,
    }

    metadata = {
        'producto': 'DPV Integrado MetVlc',
        'descripcion': 'Visor integrado con estaciones seleccionadas de AEMET, AVAMET y Meteoclimatic. Las estaciones sin dato actual se mantienen en gris.',
        'generado_utc': datetime.now(timezone.utc).isoformat(),
        'modo_sin_datos': 'Opción A: si no hay dato actual, la estación aparece como Sin datos.',
        'max_edad_horas_fuente': MAX_EDAD_HORAS,
        'fuentes': estado_fuentes,
        'resumen': resumen,
        'formula': 'DPV = es - ea; es = 0.6108 * exp((17.27*T)/(T+237.3)); ea = es * HR/100',
    }

    OUT_GEOJSON.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding='utf-8')
    OUT_METADATA.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

    print('Total:', resumen['total'])
    print('Con datos:', resumen['con_datos'])
    print('Sin datos:', resumen['sin_datos'])
    print('GeoJSON generado:', OUT_GEOJSON)
    print('Metadata generado:', OUT_METADATA)


if __name__ == '__main__':
    main()
