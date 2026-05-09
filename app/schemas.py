from pydantic import BaseModel, Field, field_validator
from typing import Optional, List


class CoordenadasInput(BaseModel):
    lat: float = Field(..., ge=-60, le=-15, description="Latitud (América del Sur: -60 a -15)")
    lng: float = Field(..., ge=-80, le=-34, description="Longitud (América del Sur: -80 a -34)")
    hectareas: Optional[float] = Field(None, gt=0, description="Hectáreas del lote — ajusta el área mostrada en el mapa")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "lat": -31.4167,
                    "lng": -64.1833,
                    "description": "Córdoba, Argentina",
                },
                {
                    "lat": -34.6037,
                    "lng": -58.3816,
                    "description": "Buenos Aires, Argentina",
                },
            ]
        }
    }


# ── Respuesta NDVI punto ──────────────────────────────────────────────────────

class NDVIPuntoResponse(BaseModel):
    ndvi: float = Field(..., description="Valor NDVI entre -1 y 1 (vegetación: 0 a 1)")
    categoria: str = Field(..., description="optimo | bueno | atencion | alerta | sin_vegetacion")
    color: str = Field(..., description="Color hex del sistema de diseño AgrowIArk")
    descripcion: str = Field(..., description="Texto legible del estado de la vegetación")
    fecha_imagen: str = Field(..., description="Fecha de la imagen usada (YYYY-MM-DD)")
    dias_desde_imagen: int = Field(..., description="Días desde que se tomó la imagen")
    nubosidad_pct: float = Field(..., description="Porcentaje de nubosidad de la escena")
    satelite: str = Field(default="Sentinel-2 L2A")
    fuente: str = Field(default="Microsoft Planetary Computer")
    lat: float
    lng: float


# ── Serie temporal NDVI ───────────────────────────────────────────────────────

class NDVIPuntoEnSerie(BaseModel):
    fecha: str = Field(..., description="Mes de la medición (YYYY-MM)")
    ndvi: float
    categoria: str
    color: str
    nubosidad_pct: float


class NDVISerieResponse(BaseModel):
    lat: float
    lng: float
    meses_solicitados: int
    total_escenas: int
    serie: List[NDVIPuntoEnSerie]


# ── Resumen IA (Groq) ─────────────────────────────────────────────────────────

class ResumenIAInput(BaseModel):
    nombre: str
    cultivo: str
    lat: float
    lng: float
    ndvi_val: float
    categoria: str
    descripcion: str
    fecha_imagen: str
    dias_desde_imagen: int
    nubosidad_pct: float
    hectareas: Optional[float] = None
    provincia: Optional[str] = None
    fecha_siembra: Optional[str] = None   # YYYY-MM-DD
    notas_ia: Optional[str] = None        # texto libre para el prompt


# ── Error ─────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detalle: Optional[str] = None
    sugerencia: Optional[str] = None
