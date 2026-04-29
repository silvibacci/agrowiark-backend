"""
run.py — Iniciador del servidor de desarrollo AgrowIArk

Uso:
  python run.py

El servidor se levanta en http://localhost:8000
Documentación interactiva en http://localhost:8000/docs
"""
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
