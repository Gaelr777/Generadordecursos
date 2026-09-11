"""
Orquestación de las llamadas al modelo.

Tres decisiones que sostienen el costo:
  1. El bloque SYSTEM va con cache_control: es idéntico entre módulos y se
     cobra completo una sola vez.
  2. Las etapas que solo dependen del syllabus (módulos, marco, evaluaciones,
     presentación) se lanzan en paralelo con asyncio.gather.
  3. Cuando la validación falla, se reintenta SOLO esa etapa, pasando los
     errores concretos como contexto. Nunca se regenera el curso completo.
"""

import asyncio
import json
import os
import re
from typing import Any, Callable

from anthropic import AsyncAnthropic

from . import prompts as P
from . import validate as V

MAX_REINTENTOS = 2


class ErrorGeneracion(Exception):
    pass


def _extraer_json(texto: str) -> dict:
    """El modelo debe devolver JSON puro, pero toleramos envoltorios."""
    t = texto.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            return json.loads(t[i:j + 1])
        raise


class Generador:
    def __init__(self, api_key: str, modelo: str, cfg: dict,
                 on_event: Callable[[dict], None] | None = None):
        self.client = AsyncAnthropic(api_key=api_key)
        self.modelo = modelo
        self.cfg = cfg
        self.on_event = on_event or (lambda e: None)
        self.uso = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "llamadas": 0}

    def _evento(self, etapa: str, estado: str, **extra):
        self.on_event({"etapa": etapa, "estado": estado, **extra})

    async def _llamar(self, system: str, user: str, max_tokens: int = 16000) -> dict:
        resp = await self.client.messages.create(
            model=self.modelo,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        u = resp.usage
        self.uso["llamadas"] += 1
        self.uso["input"] += getattr(u, "input_tokens", 0) or 0
        self.uso["output"] += getattr(u, "output_tokens", 0) or 0
        self.uso["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.uso["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        return _extraer_json(resp.content[0].text)

    async def _con_validacion(self, etapa: str, system: str, user: str,
                              validador: Callable[[dict], list[dict]],
                              max_tokens: int = 16000) -> tuple[dict, list[dict]]:
        """Genera, valida, y reintenta solo esta etapa si hay errores bloqueantes."""
        contexto_error = ""
        resultado, hallazgos = None, []

        for intento in range(MAX_REINTENTOS + 1):
            self._evento(etapa, "generando", intento=intento + 1)
            try:
                resultado = await self._llamar(system, user + contexto_error, max_tokens)
            except json.JSONDecodeError as e:
                if intento == MAX_REINTENTOS:
                    raise ErrorGeneracion(f"{etapa}: el modelo no devolvió JSON válido ({e})")
                contexto_error = ("\n\nTu respuesta anterior no fue JSON válido. "
                                  "Devuelve ÚNICAMENTE el objeto JSON, sin texto ni ```.")
                continue

            hallazgos = validador(resultado)
            errores = [h for h in hallazgos if h["severidad"] == "error"]
            if not errores:
                self._evento(etapa, "ok", avisos=len(hallazgos))
                return resultado, hallazgos

            if intento == MAX_REINTENTOS:
                self._evento(etapa, "ok_con_errores", errores=len(errores))
                return resultado, hallazgos

            self._evento(etapa, "reintentando", errores=len(errores))
            detalle = "\n".join(f"- {e['ruta']}: {e['regla']} — {e['detalle']}" for e in errores[:15])
            contexto_error = (
                "\n\n--- CORRECCIÓN REQUERIDA ---\n"
                "Tu salida anterior falló estas verificaciones automáticas:\n"
                f"{detalle}\n"
                "Devuelve el JSON completo corregido. Ajusta SOLO lo señalado; "
                "conserva el resto del contenido tal cual."
            )
        return resultado, hallazgos

    # ---------------------------------------------------------------- etapas

    async def ficha(self, texto_fuente: str, nombre_curso: str,
                    modo: str = "ingesta", bibliografia: str = "(sin bibliografía previa)") -> dict:
        system = P.bloque_sistema(self.cfg, P.E1_ROL)
        user = P.E1_USER.format(modo=modo, nombre_curso=nombre_curso or "(tómalo del documento)",
                                fuente=texto_fuente, bibliografia=bibliografia)
        r, _ = await self._con_validacion("E1", system, user, lambda d: [])
        return r

    async def syllabus(self, ficha: dict, bibliografia: str = "(usa la de la ficha)") -> dict:
        system = P.bloque_sistema(self.cfg, P.E2_ROL)
        user = P.E2_USER.format(ficha=json.dumps(ficha, ensure_ascii=False),
                                bibliografia=bibliografia)
        r, _ = await self._con_validacion("E2", system, user, lambda d: [])
        return r

    async def modulo(self, syllabus: dict, n: int) -> tuple[dict, list[dict]]:
        system = P.bloque_sistema(self.cfg, P.E3_ROL)
        user = P.E3_USER.format(n=n, syllabus=json.dumps(syllabus, ensure_ascii=False))
        return await self._con_validacion(
            f"E3.M{n}", system, user,
            lambda d: V.validar_modulo(d, self.cfg, syllabus), max_tokens=20000)

    async def marco(self, syllabus: dict, facilitador_nombre: str,
                    facilitador_datos: str) -> tuple[dict, list[dict]]:
        system = P.bloque_sistema(self.cfg, P.E4_ROL)
        user = P.E4_USER.format(
            syllabus=json.dumps(syllabus, ensure_ascii=False),
            facilitador_nombre=facilitador_nombre or "[POR DEFINIR]",
            facilitador_datos=facilitador_datos or "(no se proporcionaron datos)")
        return await self._con_validacion("E4", system, user,
                                          lambda d: V.validar_marco(d, self.cfg))

    async def evaluaciones(self, syllabus: dict) -> tuple[dict, list[dict]]:
        system = P.bloque_sistema(self.cfg, P.E5_ROL)
        user = P.E5_USER.format(syllabus=json.dumps(syllabus, ensure_ascii=False))
        return await self._con_validacion("E5", system, user,
                                          lambda d: V.validar_evaluaciones(d, self.cfg),
                                          max_tokens=20000)

    async def video(self, syllabus: dict, n: int) -> dict:
        system = P.bloque_sistema(self.cfg, P.E6_ROL)
        user = P.E6_USER.format(n=n, syllabus=json.dumps(syllabus, ensure_ascii=False))
        r, _ = await self._con_validacion(f"E6.M{n}", system, user, lambda d: [])
        return r

    async def presentacion(self, syllabus: dict, marco: dict,
                           modalidad: str) -> tuple[dict, list[dict]]:
        md = P.MODALIDADES[modalidad]
        ds = P.load_design_system()
        system = P.bloque_sistema(self.cfg, P.E7_ROL,
                                  extra="SISTEMA DE DISEÑO:\n" + json.dumps(ds, ensure_ascii=False))
        user = P.E7_USER.format(
            etiqueta=md["etiqueta"], prompt_playbook=md["prompt_playbook"], nota=md["nota"],
            practicas_por_tema=md["practicas_por_tema"], columnas=", ".join(md["columnas"]),
            syllabus=json.dumps(syllabus, ensure_ascii=False),
            marco=json.dumps(marco, ensure_ascii=False) if marco else "(no generado)")
        horas = float((syllabus.get("identificacion") or {}).get("duracion_horas") or 0)
        return await self._con_validacion(
            f"E7.{modalidad}", system, user,
            lambda d: V.validar_presentacion(d, self.cfg, horas), max_tokens=24000)

    # ------------------------------------------------------------- pipeline

    async def curso_completo(self, texto_ficha: str, opciones: dict) -> dict:
        """
        Ejecuta el pipeline. E1 → E2 son secuenciales; todo lo demás
        se lanza junto porque solo depende del syllabus.
        """
        out: dict[str, Any] = {"hallazgos": {}, "alertas": {}}

        ficha = await self.ficha(texto_ficha, opciones.get("nombre_curso", ""),
                                 modo=opciones.get("modo", "ingesta"))
        out["ficha"] = ficha
        out["alertas"]["ficha"] = ficha.get("alertas", [])

        syllabus = await self.syllabus(ficha)
        out["syllabus"] = syllabus
        out["alertas"]["syllabus"] = syllabus.get("alertas", [])

        n_modulos = len(syllabus.get("modulos") or ficha.get("modulos") or [])
        if not n_modulos:
            raise ErrorGeneracion("El syllabus no tiene módulos; revisa la ficha de origen.")

        tareas: dict[str, Any] = {}
        for i in range(1, n_modulos + 1):
            tareas[f"modulo_{i}"] = self.modulo(syllabus, i)
        if opciones.get("marco", True):
            tareas["marco"] = self.marco(syllabus, opciones.get("facilitador_nombre", ""),
                                         opciones.get("facilitador_datos", ""))
        if opciones.get("evaluaciones", True):
            tareas["evaluaciones"] = self.evaluaciones(syllabus)
        if opciones.get("video", False):
            for i in range(1, n_modulos + 1):
                tareas[f"video_{i}"] = self.video(syllabus, i)

        self._evento("paralelo", "lanzando", tareas=len(tareas))
        resultados = await asyncio.gather(*tareas.values(), return_exceptions=True)

        for clave, res in zip(tareas.keys(), resultados):
            if isinstance(res, Exception):
                out.setdefault("errores", {})[clave] = str(res)
                self._evento(clave, "error", detalle=str(res))
                continue
            if isinstance(res, tuple):
                dato, hallazgos = res
                out[clave] = dato
                out["hallazgos"][clave] = hallazgos
            else:
                out[clave] = res

        modalidades = opciones.get("presentaciones") or []
        if modalidades:
            tareas_p = {m: self.presentacion(syllabus, out.get("marco"), m) for m in modalidades}
            self._evento("presentaciones", "lanzando", tareas=len(tareas_p))
            res_p = await asyncio.gather(*tareas_p.values(), return_exceptions=True)
            for m, res in zip(tareas_p.keys(), res_p):
                if isinstance(res, Exception):
                    out.setdefault("errores", {})[f"presentacion_{m}"] = str(res)
                    continue
                dato, hallazgos = res
                out[f"presentacion_{m}"] = dato
                out["hallazgos"][f"presentacion_{m}"] = hallazgos

        out["uso"] = self.uso
        return out


async def listar_modelos(api_key: str) -> list[dict]:
    client = AsyncAnthropic(api_key=api_key)
    page = await client.models.list(limit=50)
    return [{"id": m.id, "nombre": getattr(m, "display_name", m.id)} for m in page.data]
