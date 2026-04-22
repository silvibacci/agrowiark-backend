"""
run.py — Iniciador del servidor de desarrollo AgrowIArk

Uso:
  python run.py

El servidor se levanta en http://localhost:8000
Documentación interactiva en http://localhost:8000/docs
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,        # Recarga automática al modificar archivos
        log_level="info",
    )
