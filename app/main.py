from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import lotes

app = FastAPI(
    title="AgrowIArk API",
    description=(
        "Backend de AgrowIArk — Análisis satelital + gestión de establecimiento.\n\n"
        "Usa Microsoft Planetary Computer (Sentinel-2, gratuito) para NDVI "
        "y Open-Meteo (gratuito) para clima."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción restringir a tu dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lotes.router, prefix="/api/v1/lotes", tags=["Lotes"])


@app.get("/", tags=["Estado"])
def root():
    return {
        "proyecto": "AgrowIArk",
        "version": "0.1.0",
        "estado": "ok",
        "docs": "/docs",
    }


@app.get("/health", tags=["Estado"])
def health():
    return {"status": "ok"}
