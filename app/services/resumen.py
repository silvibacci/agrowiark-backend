"""
resumen.py — Generación de resumen agronómico con Groq (llama-3.3-70b-versatile)
"""
import os
import json
from datetime import date as date_type
from groq import Groq


def _etapa_fenologica(cultivo: str, fecha_siembra: str, fecha_imagen: str) -> str | None:
    """
    Calcula la etapa fenológica del cultivo en la fecha de la imagen satelital.
    Devuelve una descripción de la etapa + días desde siembra (DDS).
    Si la etapa implica senescencia natural, lo aclara explícitamente para que
    la IA no confunda NDVI bajo con estrés hídrico o nutricional.
    """
    try:
        siembra = date_type.fromisoformat(fecha_siembra)
        imagen  = date_type.fromisoformat(fecha_imagen)
        dds = (imagen - siembra).days
    except Exception:
        return None

    if dds < 0:
        return f"Siembra programada (faltan {abs(dds)} días)"

    c = cultivo.lower()

    if "soja" in c:
        if dds < 15:   return f"VE-VC: emergencia e implantación ({dds} DDS)"
        elif dds < 40: return f"V2-V4: desarrollo vegetativo inicial ({dds} DDS)"
        elif dds < 65: return f"V6-V8: crecimiento vegetativo activo ({dds} DDS)"
        elif dds < 90: return f"R1-R3: floración e inicio de vaina ({dds} DDS)"
        elif dds < 120: return f"R4-R5: llenado de grano ({dds} DDS)"
        elif dds < 150: return (
            f"R6-R7: madurez fisiológica / senescencia ({dds} DDS) — "
            "NDVI bajo es COMPLETAMENTE NORMAL en esta etapa, la planta está "
            "secando naturalmente, no indica estrés ni falta de agua"
        )
        else: return f"R8 / cosecha o post-cosecha ({dds} DDS) — NDVI bajo es esperado"

    elif "maíz" in c or "maiz" in c:
        if dds < 20:    return f"VE: emergencia ({dds} DDS)"
        elif dds < 55:  return f"V6-V10: desarrollo vegetativo ({dds} DDS)"
        elif dds < 85:  return f"VT-R1: floración / polinización ({dds} DDS)"
        elif dds < 125: return f"R2-R4: llenado de grano ({dds} DDS)"
        elif dds < 160: return (
            f"R5-R6: madurez fisiológica / secado ({dds} DDS) — "
            "NDVI bajo es NORMAL, el cultivo está secando hacia cosecha"
        )
        else: return f"Post-cosecha ({dds} DDS)"

    elif "trigo" in c:
        if dds < 30:    return f"Implantación / macollaje inicial ({dds} DDS)"
        elif dds < 85:  return f"Macollaje / encañazón ({dds} DDS)"
        elif dds < 115: return f"Espigado / floración ({dds} DDS)"
        elif dds < 150: return f"Llenado de grano ({dds} DDS)"
        elif dds < 180: return (
            f"Madurez / cosecha ({dds} DDS) — "
            "NDVI bajo es NORMAL, el cultivo está madurando"
        )
        else: return f"Post-cosecha ({dds} DDS)"

    elif "girasol" in c:
        if dds < 25:    return f"Emergencia ({dds} DDS)"
        elif dds < 60:  return f"Desarrollo vegetativo ({dds} DDS)"
        elif dds < 90:  return f"Floración ({dds} DDS)"
        elif dds < 130: return (
            f"Madurez / cosecha ({dds} DDS) — "
            "NDVI bajo es NORMAL en esta etapa"
        )
        else: return f"Post-cosecha ({dds} DDS)"

    else:
        return f"{dds} días desde siembra"


def get_resumen_ia(
    nombre: str,
    cultivo: str,
    lat: float,
    lng: float,
    ndvi_val: float,
    categoria: str,
    descripcion: str,
    fecha_imagen: str,
    dias_desde_imagen: int,
    nubosidad_pct: float,
    hectareas: float | None = None,
    provincia: str | None = None,
    fecha_siembra: str | None = None,
    notas_ia: str | None = None,
) -> dict:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key or api_key.startswith("gsk_REEMPLAZAR"):
        raise ValueError("GROQ_API_KEY no configurada. Editá el archivo .env con tu clave de https://console.groq.com")

    client = Groq(api_key=api_key)

    ha_str   = f"{hectareas:.1f} ha" if hectareas else "superficie no especificada"
    prov_str = f" ({provincia})" if provincia else ""

    # Etapa fenológica — clave para interpretar NDVI correctamente
    etapa = _etapa_fenologica(cultivo, fecha_siembra, fecha_imagen) if fecha_siembra else None
    etapa_str = f"\n- Etapa fenológica: {etapa}" if etapa else ""

    # Notas libres del productor
    notas_str = f"\n\nINFORMACIÓN ADICIONAL DEL PRODUCTOR:\n{notas_ia}" if notas_ia else ""

    # Regla crítica si el cultivo está en senescencia
    regla_fenologia = ""
    if etapa and ("senescencia" in etapa or "cosecha" in etapa or "secando" in etapa or "NORMAL" in etapa):
        regla_fenologia = (
            "\n- ⚠️ CRÍTICO: El cultivo está en etapa de madurez/senescencia. "
            "El NDVI bajo es ESPERADO y NORMAL en esta fase — NO lo interpretes como estrés hídrico ni problema. "
            "La condicion_global debe reflejar el estado fenológico, no comparar con NDVI de cultivo en crecimiento."
        )

    prompt = f"""Sos un agrónomo experto en teledetección y análisis satelital agrícola en Argentina.
Generá un informe técnico conciso y accionable para el siguiente lote:{notas_str}

DATOS DEL LOTE:
- Nombre: {nombre}{prov_str}
- Cultivo: {cultivo}
- Superficie: {ha_str}
- Coordenadas: {lat:.4f}°, {lng:.4f}°{etapa_str}

DATOS SATELITALES (Sentinel-2 L2A via Planetary Computer):
- NDVI: {ndvi_val:.3f}
- Estado satelital: {categoria} — {descripcion}
- Fecha imagen: {fecha_imagen} (hace {dias_desde_imagen} días)
- Nubosidad de la escena: {nubosidad_pct}%

INSTRUCCIONES:
- Respondé ÚNICAMENTE con JSON válido, sin texto adicional fuera del JSON
- El análisis debe ser técnico pero entendible para un productor agropecuario
- Usá terminología agronómica argentina
- Interpretá el NDVI SIEMPRE en contexto de la etapa fenológica indicada{regla_fenologia}
- Si no hay fecha de siembra: interpretá según NDVI (alto >0.6 = óptimo, medio 0.4-0.6 = monitorear, bajo <0.4 = alerta)
- El campo "puntos" debe tener exactamente 4 elementos (strings, no arrays)
- El campo "recomendacion" es la acción más importante y concreta a tomar HOY

Formato JSON requerido:
{{
  "resumen": "párrafo de 2-3 oraciones mencionando nombre del lote, cultivo, etapa fenológica y NDVI con su interpretación correcta",
  "puntos": [
    "análisis de cobertura vegetal en contexto de la etapa del cultivo",
    "interpretación hídrica considerando las necesidades actuales del cultivo",
    "perspectiva de rendimiento y proyección hacia cosecha",
    "recomendaciones de manejo para la etapa actual"
  ],
  "condicion_global": "optima|buena|atencion|alerta",
  "recomendacion": "acción concreta y específica que el productor debe tomar considerando la etapa fenológica"
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.25,
        max_tokens=700,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    return json.loads(raw)
