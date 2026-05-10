"""
satellite.py — Servicio de análisis satelital para AgrowIArk

Fuente de imágenes: Microsoft Planetary Computer (gratuito)
Colección: Sentinel-2 L2A (10m de resolución, revisita ~5 días)
Bandas usadas:
  B04 → Red  (665 nm)
  B08 → NIR  (842 nm)
NDVI = (NIR - Red) / (NIR + Red)
"""

import base64
import io
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import planetary_computer
import pystac_client
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.env import Env
from rasterio.transform import rowcol

logger = logging.getLogger(__name__)

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLECCION = "sentinel-2-l2a"

# Configuración GDAL para lecturas COG remotas más estables
GDAL_CONFIG = {
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_CONNECTTIMEOUT": "15",
    "GDAL_HTTP_MAX_RETRY": "1",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_CHUNK_SIZE": "524288",  # 512KB chunks — menor latencia inicial
}


# ── Cliente STAC ──────────────────────────────────────────────────────────────

def _cliente_stac() -> pystac_client.Client:
    return pystac_client.Client.open(
        STAC_URL,
        modifier=planetary_computer.sign_inplace,
    )


# ── Lectura de píxel desde COG ────────────────────────────────────────────────

def _leer_pixel(href: str, lat: float, lng: float, ventana: int = 5) -> float:
    """
    Lee el valor promedio de una ventana (ventana x ventana píxeles)
    centrada en las coordenadas dadas, desde un COG de Sentinel-2.
    La ventana de 5x5 evita errores en bordes y ruido por píxel individual.
    """
    with Env(**GDAL_CONFIG), rasterio.open(href) as src:
        # Reproyectar WGS84 → CRS nativo de la imagen (UTM)
        transformer = Transformer.from_crs(
            "EPSG:4326", src.crs.to_epsg() or src.crs.to_wkt(),
            always_xy=True
        )
        x, y = transformer.transform(lng, lat)

        # Convertir coordenadas proyectadas a fila/columna del raster
        row, col = rowcol(src.transform, x, y)

        # Ventana centrada, con clamp a bordes del tile
        mitad = ventana // 2
        col_off = max(0, col - mitad)
        row_off = max(0, row - mitad)
        col_off = min(col_off, src.width - ventana)
        row_off = min(row_off, src.height - ventana)

        win = rasterio.windows.Window(col_off, row_off, ventana, ventana)
        data = src.read(1, window=win).astype(np.float32)

        # Filtrar no-data (Sentinel-2 usa 0 como nodata en L2A)
        data = data[data > 0]
        return float(np.mean(data)) if len(data) > 0 else 0.0


# ── Cálculo y clasificación NDVI ─────────────────────────────────────────────

def _calcular_ndvi(red: float, nir: float) -> float:
    denominador = nir + red
    if denominador == 0:
        return 0.0
    return round((nir - red) / denominador, 4)


def _clasificar_ndvi(ndvi: float) -> dict:
    """
    Clasificación según el sistema de diseño AgrowIArk.
    Colores exactos de la paleta definida en SKILL.md.
    """
    if ndvi >= 0.6:
        return {
            "categoria": "optimo",
            "color": "#39ff6a",
            "descripcion": "Vegetación en condición óptima — cobertura densa y saludable",
        }
    elif ndvi >= 0.4:
        return {
            "categoria": "bueno",
            "color": "#7fff45",
            "descripcion": "Vegetación en buen estado — leve estrés o cultivo en desarrollo",
        }
    elif ndvi >= 0.2:
        return {
            "categoria": "atencion",
            "color": "#ffb800",
            "descripcion": "Vegetación con estrés moderado — monitorear en los próximos días",
        }
    elif ndvi >= 0.0:
        return {
            "categoria": "alerta",
            "color": "#ff4545",
            "descripcion": "Vegetación con estrés severo o suelo con escasa cobertura",
        }
    else:
        return {
            "categoria": "sin_vegetacion",
            "color": "#6b7d65",
            "descripcion": "Sin vegetación detectable — agua, suelo desnudo o nieve",
        }


# ── Búsqueda de escena óptima ─────────────────────────────────────────────────

def _buscar_mejor_escena(
    catalog: pystac_client.Client,
    lat: float,
    lng: float,
    fecha_inicio: str,
    fecha_fin: str,
    nube_max: int = 60,
    max_items: int = 10,
) -> Optional[object]:
    """
    Busca la escena Sentinel-2 más reciente con nubosidad aceptable.
    Prioriza FECHA RECIENTE sobre claridad: para agricultura, una imagen
    de hace 5 días con 30% nubes es más útil que una de hace 3 semanas
    con 0% nubes.
    Score = días_desde_imagen * 2 + nubosidad_pct  (menor = mejor)
    """
    bbox = [lng - 0.05, lat - 0.05, lng + 0.05, lat + 0.05]

    search = catalog.search(
        collections=[COLECCION],
        bbox=bbox,
        datetime=f"{fecha_inicio}/{fecha_fin}",
        query={"eo:cloud_cover": {"lt": nube_max}},
        sortby="-datetime",
        max_items=max_items,
    )

    items = list(search.items())
    if not items:
        return None

    ahora = datetime.now(timezone.utc)

    def score(item):
        dias = max(0, (ahora - item.datetime.replace(tzinfo=timezone.utc)).days)
        nubes = item.properties.get("eo:cloud_cover", 50)
        # Recencia pesa el doble: preferimos imagen de hace 5d/30% nubes
        # antes que imagen de hace 20d/0% nubes  (10 + 30 = 40 < 40 + 0 = 40)
        return dias * 2 + nubes

    return sorted(items, key=score)[0]


# ── API pública: NDVI punto ───────────────────────────────────────────────────

def get_ndvi_punto(lat: float, lng: float) -> dict:
    """
    Obtiene el NDVI más reciente para un punto geográfico.

    Estrategia (prioridad: recencia > claridad):
      1. Últimos 15 días, nube < 60%  → imagen de esta semana/quincena
      2. Últimos 30 días, nube < 40%  → amplía si hay mucha nubosidad sostenida
      3. Últimos 90 días, nube < 25%  → fallback otoño/invierno muy nublado
      4. ValueError si no hay nada

    Retorna un dict listo para serializar como NDVIPuntoResponse.
    """
    catalog = _cliente_stac()
    ahora = datetime.now(timezone.utc)

    # Intento 1: 15 días, nube < 60%
    item = _buscar_mejor_escena(
        catalog, lat, lng,
        fecha_inicio=(ahora - timedelta(days=15)).strftime("%Y-%m-%d"),
        fecha_fin=ahora.strftime("%Y-%m-%d"),
        nube_max=60,
    )

    # Intento 2: 30 días, nube < 40%
    if item is None:
        logger.info("No hay escenas aceptables en 15 días — ampliando a 30 días")
        item = _buscar_mejor_escena(
            catalog, lat, lng,
            fecha_inicio=(ahora - timedelta(days=30)).strftime("%Y-%m-%d"),
            fecha_fin=ahora.strftime("%Y-%m-%d"),
            nube_max=40,
        )

    # Intento 3: 90 días, nube < 25%
    if item is None:
        logger.info("No hay escenas aceptables en 30 días — ampliando a 90 días")
        item = _buscar_mejor_escena(
            catalog, lat, lng,
            fecha_inicio=(ahora - timedelta(days=90)).strftime("%Y-%m-%d"),
            fecha_fin=ahora.strftime("%Y-%m-%d"),
            nube_max=25,
        )

    if item is None:
        raise ValueError(
            f"No se encontraron imágenes Sentinel-2 disponibles para "
            f"({lat:.4f}, {lng:.4f}) en los últimos 90 días."
        )

    logger.info(
        f"Usando escena {item.id} — fecha: {item.datetime.date()} "
        f"— nube: {item.properties.get('eo:cloud_cover', '?')}%"
    )

    # Leer bandas
    b04_href = item.assets["B04"].href   # Red
    b08_href = item.assets["B08"].href   # NIR

    red = _leer_pixel(b04_href, lat, lng)
    nir = _leer_pixel(b08_href, lat, lng)
    ndvi = _calcular_ndvi(red, nir)

    clasificacion = _clasificar_ndvi(ndvi)
    fecha_img = item.datetime.replace(tzinfo=timezone.utc)
    dias_desde = (ahora - fecha_img).days

    return {
        "ndvi": ndvi,
        **clasificacion,
        "fecha_imagen": fecha_img.strftime("%Y-%m-%d"),
        "dias_desde_imagen": dias_desde,
        "nubosidad_pct": round(item.properties.get("eo:cloud_cover", 0.0), 1),
        "satelite": "Sentinel-2 L2A",
        "fuente": "Microsoft Planetary Computer",
        "lat": lat,
        "lng": lng,
    }


# ── API pública: serie temporal NDVI ─────────────────────────────────────────

def get_ndvi_serie(lat: float, lng: float, meses: int = 24) -> list:
    """
    Obtiene la serie temporal de NDVI (una medición por mes) para un punto.

    Estrategia:
      - Busca todas las escenas disponibles en el período solicitado
      - Agrupa por mes calendario
      - Para cada mes, usa la escena con menor nubosidad
      - Lee NDVI de esa escena

    Nota: puede tardar entre 20-60 segundos dependiendo de la cantidad de meses.
    Para producción, cachear con Redis o guardar en PostgreSQL.
    """
    catalog = _cliente_stac()
    ahora = datetime.now(timezone.utc)
    fecha_inicio = ahora - timedelta(days=meses * 31)

    bbox = [lng - 0.05, lat - 0.05, lng + 0.05, lat + 0.05]

    search = catalog.search(
        collections=[COLECCION],
        bbox=bbox,
        datetime=f"{fecha_inicio.strftime('%Y-%m-%d')}/{ahora.strftime('%Y-%m-%d')}",
        query={"eo:cloud_cover": {"lt": 30}},
        sortby="-datetime",
        max_items=200,
    )

    items = list(search.items())
    logger.info(f"Serie temporal: {len(items)} escenas encontradas para ({lat}, {lng})")

    if not items:
        return []

    # Agrupar por año-mes, quedar con el de menor nubosidad
    por_mes: dict[str, dict] = {}
    for item in items:
        clave = item.datetime.strftime("%Y-%m")
        nube = item.properties.get("eo:cloud_cover", 100)
        if clave not in por_mes or nube < por_mes[clave]["nube"]:
            por_mes[clave] = {"item": item, "nube": nube}

    serie = []
    for clave in sorted(por_mes.keys()):
        try:
            item = por_mes[clave]["item"]
            b04_href = item.assets["B04"].href
            b08_href = item.assets["B08"].href

            red = _leer_pixel(b04_href, lat, lng)
            nir = _leer_pixel(b08_href, lat, lng)
            ndvi = _calcular_ndvi(red, nir)
            clasificacion = _clasificar_ndvi(ndvi)

            serie.append({
                "fecha": clave,
                "ndvi": ndvi,
                "categoria": clasificacion["categoria"],
                "color": clasificacion["color"],
                "nubosidad_pct": round(por_mes[clave]["nube"], 1),
            })
            logger.info(f"  {clave}: NDVI={ndvi}")

        except Exception as exc:
            logger.warning(f"  {clave}: error al leer escena — {exc}")
            continue

    return serie


# ── Imagen NDVI coloreada ─────────────────────────────────────────────────────

def _bbox_wgs84(lat: float, lng: float, tam_px: int, res_m: float = 10.0) -> dict:
    """
    Calcula el bounding box geográfico (WGS84) de una ventana cuadrada de
    tam_px × tam_px píxeles centrada en (lat, lng), con resolución res_m m/px.
    Sentinel-2 B03/B04/B08 tienen resolución nativa de 10 m.
    """
    half_m = (tam_px / 2) * res_m
    lat_d  = half_m / 111_320
    lng_d  = half_m / (111_320 * math.cos(math.radians(lat)))
    return {
        "south": round(lat - lat_d, 6),
        "north": round(lat + lat_d, 6),
        "west":  round(lng - lng_d, 6),
        "east":  round(lng + lng_d, 6),
    }


def _leer_ventana_area(href: str, lat: float, lng: float, tam_px: int) -> np.ndarray:
    """Lee una ventana cuadrada de `tam_px` píxeles centrada en el punto."""
    with Env(**GDAL_CONFIG), rasterio.open(href) as src:
        transformer = Transformer.from_crs(
            "EPSG:4326", src.crs.to_epsg() or src.crs.to_wkt(), always_xy=True
        )
        x, y = transformer.transform(lng, lat)
        row, col = rowcol(src.transform, x, y)

        mitad = tam_px // 2
        col_off = max(0, min(col - mitad, src.width - tam_px))
        row_off = max(0, min(row - mitad, src.height - tam_px))

        win = rasterio.windows.Window(col_off, row_off, tam_px, tam_px)
        data = src.read(1, window=win).astype(np.float32)
        return data


def _ndvi_a_rgb(ndvi_array: np.ndarray) -> np.ndarray:
    """
    Convierte array NDVI a RGB usando colormap RdYlGn con estiramiento por percentiles.
    El contraste se adapta al rango real de la escena, igual que FieldData/QGIS.
    """
    h, w = ndvi_array.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)

    def lerp(a, b, t):
        a = np.array(a, dtype=np.float32)
        b = np.array(b, dtype=np.float32)
        return np.clip(a + (b - a) * t, 0, 255).astype(np.uint8)

    # Estiramiento por percentiles sobre píxeles válidos (excluye agua/sombra)
    validos = ndvi_array[ndvi_array > -0.2]
    if len(validos) > 200:
        vmin = float(np.percentile(validos, 2))
        vmax = float(np.percentile(validos, 98))
    else:
        vmin, vmax = 0.0, 0.8
    rango = max(vmax - vmin, 0.01)

    # Sin datos / agua / sombra de nube
    m_nodata = ndvi_array < -0.2
    rgb[m_nodata] = [85, 80, 75]

    # Normalizar 0-1 para el resto
    m_valid = ~m_nodata
    norm = np.zeros_like(ndvi_array)
    norm[m_valid] = np.clip((ndvi_array[m_valid] - vmin) / rango, 0, 1)

    # Colormap RdYlGn (ColorBrewer) — mismo que usan FieldData, QGIS, etc.
    # 0.00 → 0.25 : rojo oscuro → rojo
    m = m_valid & (norm < 0.25)
    t = (norm[m] / 0.25)[..., None]
    rgb[m] = lerp([165, 0, 38], [215, 48, 39], t)

    # 0.25 → 0.50 : rojo → amarillo claro
    m = m_valid & (norm >= 0.25) & (norm < 0.50)
    t = ((norm[m] - 0.25) / 0.25)[..., None]
    rgb[m] = lerp([215, 48, 39], [254, 224, 139], t)

    # 0.50 → 0.75 : amarillo claro → verde claro
    m = m_valid & (norm >= 0.50) & (norm < 0.75)
    t = ((norm[m] - 0.50) / 0.25)[..., None]
    rgb[m] = lerp([254, 224, 139], [102, 189, 99], t)

    # 0.75 → 1.00 : verde claro → verde oscuro
    m = m_valid & (norm >= 0.75)
    t = ((norm[m] - 0.75) / 0.25)[..., None]
    rgb[m] = lerp([102, 189, 99], [26, 152, 80], t)

    return rgb


def _tam_px_para_ha(hectareas: float, con_margen: bool = False) -> int:
    """
    Tamaño de ventana en píxeles para el lote.
    con_margen=False → ventana exacta del lote (para mostrar solo el lote).
    con_margen=True  → agrega 80% de margen para contexto.
    """
    lado_px = int(math.sqrt(hectareas * 10_000) / 10)
    if con_margen:
        lado_px = int(lado_px * 1.8)
    return max(128, min(300, lado_px))


def _dibujar_contorno_lote(rgb: np.ndarray, hectareas: Optional[float]) -> np.ndarray:
    """
    Dibuja un rectángulo blanco que indica el área aproximada del lote.
    El rectángulo ocupa la proporción del lote dentro de la ventana visible.
    """
    h, w = rgb.shape[:2]

    if hectareas:
        tam_px = _tam_px_para_ha(hectareas)
        # Proporción: cuánto del ancho de la ventana ocupa el lote sin margen
        prop = math.sqrt(hectareas * 10_000) / 10 / tam_px
        prop = min(0.90, max(0.30, prop))
    else:
        prop = 0.70  # Si no hay hectáreas, marcar el 70% central

    lado_h = int(h * prop)
    lado_w = int(w * prop)
    y0 = (h - lado_h) // 2
    x0 = (w - lado_w) // 2
    y1 = y0 + lado_h - 1
    x1 = x0 + lado_w - 1

    color = [255, 255, 255]
    grosor = 4

    for t in range(grosor):
        # Bordes horizontales
        if 0 <= y0 + t < h:
            rgb[y0 + t, max(0, x0):min(w, x1 + 1)] = color
        if 0 <= y1 - t < h:
            rgb[y1 - t, max(0, x0):min(w, x1 + 1)] = color
        # Bordes verticales
        if 0 <= x0 + t < w:
            rgb[max(0, y0):min(h, y1 + 1), x0 + t] = color
        if 0 <= x1 - t < w:
            rgb[max(0, y0):min(h, y1 + 1), x1 - t] = color

    return rgb


def get_ndvi_mapa(lat: float, lng: float, hectareas: Optional[float] = None) -> dict:
    """
    Genera una imagen PNG del mapa NDVI para el área del lote.
    Si se proveen hectáreas, ajusta la ventana para mostrar solo el lote.
    """
    # Sin hectáreas usamos ventana fija; con hectáreas leemos exactamente el lote
    tam_px = _tam_px_para_ha(hectareas) if hectareas else 160
    catalog = _cliente_stac()
    ahora = datetime.now(timezone.utc)

    # Buscar imagen más reciente con hasta 60 días de antigüedad
    item = _buscar_mejor_escena(
        catalog, lat, lng,
        fecha_inicio=(ahora - timedelta(days=60)).strftime("%Y-%m-%d"),
        fecha_fin=ahora.strftime("%Y-%m-%d"),
        nube_max=60,
    )
    if item is None:
        item = _buscar_mejor_escena(
            catalog, lat, lng,
            fecha_inicio=(ahora - timedelta(days=90)).strftime("%Y-%m-%d"),
            fecha_fin=ahora.strftime("%Y-%m-%d"),
            nube_max=40,
        )
    if item is None:
        raise ValueError("No hay imágenes disponibles para generar el mapa.")

    logger.info(f"Mapa NDVI: usando escena {item.id} — {item.datetime.date()}")

    b04_href = item.assets["B04"].href
    b08_href = item.assets["B08"].href

    red_arr = _leer_ventana_area(b04_href, lat, lng, tam_px)
    nir_arr = _leer_ventana_area(b08_href, lat, lng, tam_px)

    # NDVI por píxel
    denom = nir_arr + red_arr
    denom[denom == 0] = 1e-6
    ndvi_arr = (nir_arr - red_arr) / denom

    # Calcular estadísticas del área
    ndvi_validos = ndvi_arr[ndvi_arr > 0]
    ndvi_medio = float(np.mean(ndvi_validos)) if len(ndvi_validos) > 0 else 0.0
    ndvi_min = float(np.percentile(ndvi_validos, 5)) if len(ndvi_validos) > 0 else 0.0
    ndvi_max = float(np.percentile(ndvi_validos, 95)) if len(ndvi_validos) > 0 else 0.0
    pct_estres = float(np.mean(ndvi_arr < 0.3) * 100)

    # Convertir a imagen RGB y escalar
    rgb = _ndvi_a_rgb(ndvi_arr)
    img = Image.fromarray(rgb, "RGB")
    img = img.resize((512, 512), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode()

    clasificacion = _clasificar_ndvi(ndvi_medio)

    return {
        "imagen_b64": img_b64,
        "fecha_imagen": item.datetime.strftime("%Y-%m-%d"),
        "nubosidad_pct": round(item.properties.get("eo:cloud_cover", 0), 1),
        "ndvi_medio": round(ndvi_medio, 4),
        "ndvi_min": round(ndvi_min, 4),
        "ndvi_max": round(ndvi_max, 4),
        "pct_estres": round(pct_estres, 1),
        "satelite": "Sentinel-2 L2A",
        **clasificacion,
        "lat": lat,
        "lng": lng,
        "bbox": _bbox_wgs84(lat, lng, tam_px),
    }


# ── Mapa NDWI (agua y humedad) ────────────────────────────────────────────────

def _ndwi_a_rgb(ndwi_array: np.ndarray) -> np.ndarray:
    """
    Convierte array NDWI (float, -1 a 1) a imagen RGB:
      < -0.3   → marrón/beige (suelo muy seco)
      -0.3–0   → verde oscuro (vegetación sin agua superficial)
      0–0.3    → cian/teal (#00e5cc — humedad moderada)
      > 0.3    → azul intenso (agua / inundado)
    """
    h, w = ndwi_array.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)

    def lerp(a, b, t):
        a = np.array(a, dtype=np.float32)
        b = np.array(b, dtype=np.float32)
        return np.clip(a + (b - a) * t, 0, 255).astype(np.uint8)

    ndwi = np.clip(ndwi_array, -1, 1)

    # Muy seco: marrón
    m = ndwi < -0.3
    rgb[m] = [120, 80, 40]

    # Seco a neutro: marrón → verde oscuro
    m = (ndwi >= -0.3) & (ndwi < 0)
    t = ((ndwi[m] + 0.3) / 0.3)[..., None]
    rgb[m] = lerp([120, 80, 40], [20, 80, 50], t)

    # Neutro a húmedo: verde oscuro → cian
    m = (ndwi >= 0) & (ndwi < 0.3)
    t = (ndwi[m] / 0.3)[..., None]
    rgb[m] = lerp([20, 80, 50], [0, 229, 204], t)

    # Agua/inundado: cian → azul
    m = ndwi >= 0.3
    t = np.clip((ndwi[m] - 0.3) / 0.7, 0, 1)[..., None]
    rgb[m] = lerp([0, 229, 204], [20, 80, 220], t)

    return rgb


def get_ndwi_mapa(lat: float, lng: float, hectareas: Optional[float] = None) -> dict:
    """
    Genera imagen PNG del mapa NDWI (agua y humedad) para el área alrededor del punto.
    NDWI = (Green - NIR) / (Green + NIR)  —  Bandas B03 y B08 de Sentinel-2.
    """
    catalog = _cliente_stac()
    ahora = datetime.now(timezone.utc)
    tam_px = _tam_px_para_ha(hectareas) if hectareas else 160

    item = _buscar_mejor_escena(
        catalog, lat, lng,
        fecha_inicio=(ahora - timedelta(days=60)).strftime("%Y-%m-%d"),
        fecha_fin=ahora.strftime("%Y-%m-%d"),
        nube_max=60,
    )
    if item is None:
        item = _buscar_mejor_escena(
            catalog, lat, lng,
            fecha_inicio=(ahora - timedelta(days=90)).strftime("%Y-%m-%d"),
            fecha_fin=ahora.strftime("%Y-%m-%d"),
            nube_max=40,
        )
    if item is None:
        item = _buscar_mejor_escena(
            catalog, lat, lng,
            fecha_inicio=(ahora - timedelta(days=180)).strftime("%Y-%m-%d"),
            fecha_fin=ahora.strftime("%Y-%m-%d"),
            nube_max=25,
        )
    if item is None:
        raise ValueError("No hay imágenes disponibles para generar el mapa NDWI.")

    logger.info(f"Mapa NDWI: usando escena {item.id} — {item.datetime.date()}")

    b03_href = item.assets["B03"].href   # Green
    b08_href = item.assets["B08"].href   # NIR

    green_arr = _leer_ventana_area(b03_href, lat, lng, tam_px)
    nir_arr   = _leer_ventana_area(b08_href, lat, lng, tam_px)

    # NDWI por píxel
    denom = green_arr + nir_arr
    denom[denom == 0] = 1e-6
    ndwi_arr = (green_arr - nir_arr) / denom

    # Estadísticas
    ndwi_medio   = float(np.mean(ndwi_arr))
    pct_agua     = float(np.mean(ndwi_arr > 0.2) * 100)
    pct_humedad  = float(np.mean((ndwi_arr >= 0) & (ndwi_arr <= 0.2)) * 100)

    # Estado hídrico
    if ndwi_medio > 0.2:
        estado_hidrico = {"estado": "inundado", "color": "#00e5cc", "descripcion": "Presencia significativa de agua superficial"}
    elif ndwi_medio > 0:
        estado_hidrico = {"estado": "humedo", "color": "#39ff6a", "descripcion": "Buena humedad disponible"}
    elif ndwi_medio > -0.2:
        estado_hidrico = {"estado": "normal", "color": "#ffb800", "descripcion": "Humedad moderada, sin estrés hídrico"}
    else:
        estado_hidrico = {"estado": "seco", "color": "#ff4545", "descripcion": "Déficit hídrico — monitorear"}

    # Imagen — escalar
    rgb = _ndwi_a_rgb(ndwi_arr)
    img = Image.fromarray(rgb, "RGB")
    img = img.resize((512, 512), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode()

    return {
        "imagen_b64": img_b64,
        "fecha_imagen": item.datetime.strftime("%Y-%m-%d"),
        "nubosidad_pct": round(item.properties.get("eo:cloud_cover", 0), 1),
        "ndwi_medio": round(ndwi_medio, 4),
        "pct_agua": round(pct_agua, 1),
        "pct_humedad": round(pct_humedad, 1),
        "satelite": "Sentinel-2 L2A",
        **estado_hidrico,
        "lat": lat,
        "lng": lng,
        "bbox": _bbox_wgs84(lat, lng, tam_px),
    }
