from fastapi import APIRouter, HTTPException, Query
from app.schemas import CoordenadasInput, NDVIPuntoResponse, NDVISerieResponse, ResumenIAInput
from app.services import satellite, clima as clima_svc, resumen as resumen_svc

router = APIRouter()


@router.post(
    "/ndvi",
    response_model=NDVIPuntoResponse,
    summary="NDVI actual de un punto",
    description=(
        "Consulta el NDVI más reciente para un punto geográfico usando "
        "imágenes Sentinel-2 L2A vía Microsoft Planetary Computer (gratuito). "
        "Tiempo de respuesta estimado: 3-8 segundos."
    ),
)
def consultar_ndvi(coords: CoordenadasInput):
    try:
        resultado = satellite.get_ndvi_punto(coords.lat, coords.lng)
        return resultado
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Sin imágenes disponibles",
                "detalle": str(exc),
                "sugerencia": (
                    "Verificá que las coordenadas estén en el área de cobertura "
                    "(Argentina y países limítrofes) o probá con coordenadas diferentes."
                ),
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Error al consultar imágenes satelitales",
                "detalle": str(exc),
            },
        )


@router.post(
    "/ndvi/serie",
    response_model=NDVISerieResponse,
    summary="Serie temporal NDVI (mensual)",
    description=(
        "Retorna la evolución mensual del NDVI para un punto geográfico. "
        "Útil para graficar el estado de la vegetación a lo largo del tiempo. "
        "⚠️ Puede tardar entre 30 y 90 segundos según la cantidad de meses. "
        "Para producción, cachear el resultado en base de datos."
    ),
)
def consultar_ndvi_serie(
    coords: CoordenadasInput,
    meses: int = Query(
        default=24,
        ge=1,
        le=60,
        description="Cantidad de meses históricos (1-60)",
    ),
):
    try:
        serie = satellite.get_ndvi_serie(coords.lat, coords.lng, meses=meses)
        return {
            "lat": coords.lat,
            "lng": coords.lng,
            "meses_solicitados": meses,
            "total_escenas": len(serie),
            "serie": serie,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Error al generar serie temporal",
                "detalle": str(exc),
            },
        )


@router.post(
    "/ndvi-mapa",
    summary="Imagen NDVI real del área (PNG base64)",
    description=(
        "Genera una imagen PNG del mapa NDVI real para el área alrededor del punto, "
        "usando píxeles reales de Sentinel-2. "
        "Retorna la imagen en base64 lista para mostrar en un <img> tag. "
        "Tiempo estimado: 5-15 segundos."
    ),
)
def consultar_ndvi_mapa(coords: CoordenadasInput):
    try:
        resultado = satellite.get_ndvi_mapa(coords.lat, coords.lng)
        return resultado
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "Error al generar mapa NDVI", "detalle": str(exc)},
        )


@router.post(
    "/ndwi-mapa",
    summary="Imagen NDWI real del área (agua y humedad)",
    description="Genera imagen PNG del mapa NDWI real usando bandas B03+B08 de Sentinel-2. Retorna base64.",
)
def consultar_ndwi_mapa(coords: CoordenadasInput):
    try:
        return satellite.get_ndwi_mapa(coords.lat, coords.lng)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "Error al generar mapa NDWI", "detalle": str(exc)},
        )


@router.post(
    "/clima",
    summary="Clima actual + pronóstico 7 días (Open-Meteo)",
    description="Retorna clima en tiempo real para las coordenadas. Fuente: Open-Meteo (gratuito, sin API key).",
)
def consultar_clima(coords: CoordenadasInput):
    try:
        return clima_svc.get_clima(coords.lat, coords.lng)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "Error al consultar clima", "detalle": str(exc)},
        )


@router.post(
    "/resumen-ia",
    summary="Resumen agronómico generado por IA (Groq / Llama 3.3)",
    description=(
        "Genera un resumen ejecutivo en lenguaje natural usando datos NDVI reales. "
        "Requiere GROQ_API_KEY en el archivo .env. "
        "Modelo: llama-3.3-70b-versatile."
    ),
)
def consultar_resumen_ia(payload: ResumenIAInput):
    try:
        resultado = resumen_svc.get_resumen_ia(
            nombre=payload.nombre,
            cultivo=payload.cultivo,
            lat=payload.lat,
            lng=payload.lng,
            ndvi_val=payload.ndvi_val,
            categoria=payload.categoria,
            descripcion=payload.descripcion,
            fecha_imagen=payload.fecha_imagen,
            dias_desde_imagen=payload.dias_desde_imagen,
            nubosidad_pct=payload.nubosidad_pct,
            hectareas=payload.hectareas,
            provincia=payload.provincia,
            fecha_siembra=payload.fecha_siembra,
            notas_ia=payload.notas_ia,
        )
        return resultado
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "IA no configurada", "detalle": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "Error al generar resumen IA", "detalle": str(exc)},
        )
