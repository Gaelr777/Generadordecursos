"""
Generador de Cursos — Club de Mentes Brillantes
Servidor local. Ver README.md para la instalación.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import extract, generate, render
from pipeline.prompts import load_config

load_dotenv()

BASE = Path(__file__).resolve().parent
SALIDAS = BASE / "salidas"
SALIDAS.mkdir(exist_ok=True)

app = FastAPI(title="Generador de Cursos — Mentes Brillantes")

# Estado en memoria. Es una herramienta local de un solo usuario:
# no hace falta base de datos, y al reiniciar se limpia solo.
TRABAJOS: dict[str, dict] = {}


def api_key() -> str:
    k = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not k:
        raise HTTPException(400, "Falta ANTHROPIC_API_KEY en el archivo .env")
    return k


# --------------------------------------------------------------------- rutas

@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/estado")
def estado():
    cfg = load_config()
    return {
        "api_key_configurada": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
        "modelo_default": os.getenv("MODELO", ""),
        "institucion": cfg.get("institucion", {}).get("nombre", ""),
        "modalidades": list(cfg.get("presentaciones", {}).get("modalidades", {}).keys()),
        "rangos": {k: v for k, v in cfg.get("rangos_palabras", {}).items()
                   if not k.startswith("_")},
    }


@app.get("/api/modelos")
async def modelos():
    try:
        return {"modelos": await generate.listar_modelos(api_key())}
    except Exception as e:
        raise HTTPException(502, f"No se pudo consultar la lista de modelos: {e}")


@app.post("/api/ficha")
async def subir_ficha(archivo: UploadFile = File(...)):
    data = await archivo.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "El archivo pesa más de 25 MB.")
    try:
        texto = extract.extraer(archivo.filename, data)
    except Exception as e:
        raise HTTPException(400, str(e))
    tid = uuid.uuid4().hex[:12]
    TRABAJOS[tid] = {"estado": "ficha_cargada", "texto_ficha": texto,
                     "archivo": archivo.filename, "eventos": []}
    return {"id": tid, "archivo": archivo.filename, **extract.resumen_extraccion(texto)}


class OpcionesGeneracion(BaseModel):
    id: str
    modelo: str
    nombre_curso: str = ""
    facilitador_nombre: str = ""
    facilitador_datos: str = ""
    marco: bool = True
    evaluaciones: bool = True
    video: bool = False
    presentaciones: list[str] = []


async def _correr(tid: str, opts: OpcionesGeneracion):
    t = TRABAJOS[tid]
    cfg = load_config()

    def on_event(e):
        t["eventos"].append(e)

    gen = generate.Generador(api_key(), opts.modelo, cfg, on_event)
    try:
        t["estado"] = "generando"
        resultado = await gen.curso_completo(t["texto_ficha"], opts.model_dump())
        t["resultado"] = resultado
        t["estado"] = "listo"
    except Exception as e:
        t["estado"] = "error"
        t["error"] = str(e)
        t["eventos"].append({"etapa": "pipeline", "estado": "error", "detalle": str(e)})


@app.post("/api/generar")
async def generar(opts: OpcionesGeneracion):
    if opts.id not in TRABAJOS:
        raise HTTPException(404, "Trabajo no encontrado; vuelve a subir la ficha.")
    api_key()
    TRABAJOS[opts.id]["eventos"] = []
    asyncio.create_task(_correr(opts.id, opts))
    return {"ok": True, "id": opts.id}


@app.get("/api/trabajo/{tid}")
def trabajo(tid: str, desde: int = 0):
    t = TRABAJOS.get(tid)
    if not t:
        raise HTTPException(404, "Trabajo no encontrado")
    r = {"estado": t["estado"], "eventos": t["eventos"][desde:],
         "total_eventos": len(t["eventos"])}
    if t.get("error"):
        r["error"] = t["error"]
    if t["estado"] == "listo":
        res = t["resultado"]
        r["resultado"] = res
        r["resumen"] = _resumen(res)
    return JSONResponse(r)


def _resumen(res: dict) -> dict:
    modulos = sorted(k for k in res if k.startswith("modulo_"))
    hall = res.get("hallazgos", {})
    total_err = sum(len([x for x in v if x["severidad"] == "error"]) for v in hall.values())
    total_avi = sum(len([x for x in v if x["severidad"] == "aviso"]) for v in hall.values())
    alertas = sum(len(v) for v in res.get("alertas", {}).values())
    return {
        "modulos": len(modulos),
        "tiene_marco": bool(res.get("marco")),
        "tiene_evaluaciones": bool(res.get("evaluaciones")),
        "presentaciones": [k.split("_", 1)[1] for k in res if k.startswith("presentacion_")],
        "errores": total_err, "avisos": total_avi, "alertas_academicas": alertas,
        "uso": res.get("uso", {}),
    }


class Edicion(BaseModel):
    id: str
    clave: str          # p.ej. "modulo_2"
    contenido: dict


@app.post("/api/editar")
def editar(e: Edicion):
    t = TRABAJOS.get(e.id)
    if not t or t.get("estado") != "listo":
        raise HTTPException(404, "No hay un resultado listo para este trabajo.")
    cfg = load_config()
    t["resultado"][e.clave] = e.contenido

    from pipeline import validate as V
    if e.clave.startswith("modulo_"):
        h = V.validar_modulo(e.contenido, cfg, t["resultado"].get("syllabus"))
    elif e.clave == "marco":
        h = V.validar_marco(e.contenido, cfg)
    elif e.clave == "evaluaciones":
        h = V.validar_evaluaciones(e.contenido, cfg)
    else:
        h = []
    t["resultado"].setdefault("hallazgos", {})[e.clave] = h
    return {"ok": True, "hallazgos": h, "resumen": _resumen(t["resultado"])}


@app.get("/api/descargar/{tid}")
def descargar(tid: str):
    t = TRABAJOS.get(tid)
    if not t or t.get("estado") != "listo":
        raise HTTPException(404, "El curso todavía no está listo.")
    res = t["resultado"]
    blob = render.paquete_zip(res)
    ident = (res.get("syllabus") or {}).get("identificacion") or {}
    nombre = f"{ident.get('codigo', 'curso')}_completo.zip".replace("/", "-")
    (SALIDAS / nombre).write_bytes(blob)
    return Response(blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


if __name__ == "__main__":
    import uvicorn
    puerto = int(os.getenv("PUERTO", "8000"))
    print(f"\n  Generador de Cursos — Club de Mentes Brillantes")
    print(f"  Abre  http://127.0.0.1:{puerto}  en tu navegador\n")
    uvicorn.run(app, host="127.0.0.1", port=puerto, log_level="warning")
