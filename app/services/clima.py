"""
clima.py — Servicio climático para AgrowIArk

Fuente: Open-Meteo API (https://open-meteo.com/)
100% gratuita, sin API key, sin límites razonables.
Resolución: coordenadas exactas del centroide del lote.
"""

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO Weather interpretation codes → (descripción, emoji)
WEATHER_CODES = {
    0:  ("Despejado", "☀️"),
    1:  ("Mayormente despejado", "🌤️"),
    2:  ("Parcialmente nublado", "⛅"),
    3:  ("Nublado", "☁️"),
    45: ("Niebla", "🌫️"),
    48: ("Niebla con escarcha", "🌫️"),
    51: ("Llovizna leve", "🌦️"),
    53: ("Llovizna moderada", "🌧️"),
    55: ("Llovizna intensa", "🌧️"),
    56: ("Llovizna helada leve", "🌧️"),
    57: ("Llovizna helada intensa", "🌧️"),
    61: ("Lluvia leve", "🌧️"),
    63: ("Lluvia moderada", "🌧️"),
    65: ("Lluvia intensa", "🌧️"),
    71: ("Nieve leve", "🌨️"),
    73: ("Nieve moderada", "🌨️"),
    75: ("Nieve intensa", "❄️"),
    77: ("Granizo menudo", "🌨️"),
    80: ("Chubascos leves", "🌦️"),
    81: ("Chubascos moderados", "🌧️"),
    82: ("Chubascos intensos", "⛈️"),
    85: ("Nieve en chubascos", "🌨️"),
    86: ("Nieve intensa en chubascos", "❄️"),
    95: ("Tormenta", "⛈️"),
    96: ("Tormenta con granizo", "⛈️"),
    99: ("Tormenta fuerte con granizo", "⛈️"),
}

WIND_DIRS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO", "N"
]


def _wind_dir_label(degrees: float) -> str:
    idx = round(degrees / 22.5) % 16
    return WIND_DIRS[idx]


def _weather_label(code: int):
    return WEATHER_CODES.get(code, ("Condición desconocida", "🌡️"))


def get_clima(lat: float, lng: float) -> dict:
    """
    Obtiene clima actual + pronóstico 7 días para las coordenadas.
    Usa Open-Meteo API (gratuita, sin key).
    """
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "weather_code",
            "wind_speed_10m",
            "wind_direction_10m",
            "precipitation",
            "cloud_cover",
        ]),
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "weather_code",
            "wind_speed_10m_max",
            "precipitation_probability_max",
        ]),
        "timezone": "America/Argentina/Buenos_Aires",
        "forecast_days": 7,
        "wind_speed_unit": "kmh",
    }

    with httpx.Client(timeout=10) as client:
        r = client.get(OPEN_METEO_URL, params=params)
        r.raise_for_status()
        data = r.json()

    current = data["current"]
    daily = data["daily"]

    wcode = current["weather_code"]
    desc, icon = _weather_label(wcode)
    wind_dir = _wind_dir_label(current.get("wind_direction_10m", 0))

    pronostico = []
    for i in range(7):
        wc = daily["weather_code"][i]
        d_desc, d_icon = _weather_label(wc)
        pronostico.append({
            "fecha": daily["time"][i],
            "icono": d_icon,
            "descripcion": d_desc,
            "temp_max": daily["temperature_2m_max"][i],
            "temp_min": daily["temperature_2m_min"][i],
            "precipitacion_mm": daily["precipitation_sum"][i] or 0,
            "prob_lluvia_pct": daily["precipitation_probability_max"][i] or 0,
            "viento_max_kmh": daily["wind_speed_10m_max"][i],
        })

    return {
        "temperatura_c": current["temperature_2m"],
        "sensacion_c": current["apparent_temperature"],
        "humedad_pct": current["relative_humidity_2m"],
        "viento_kmh": current["wind_speed_10m"],
        "viento_dir": wind_dir,
        "precipitacion_mm": current.get("precipitation", 0) or 0,
        "nubosidad_pct": current.get("cloud_cover", 0),
        "estado": desc,
        "icono": icon,
        "fuente": "Open-Meteo API",
        "lat": lat,
        "lng": lng,
        "pronostico": pronostico,
    }
