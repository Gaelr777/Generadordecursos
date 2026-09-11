"""
Validación por código de los artefactos generados.

El modelo nunca revisa su propia salida: eso lo hace este módulo. Cada hallazgo
tiene severidad `error` (bloquea) o `aviso` (se muestra pero deja continuar).
"""

import re
from difflib import SequenceMatcher

PALABRA = re.compile(r"\b[\w'áéíóúñÁÉÍÓÚÑüÜ]+\b", re.UNICODE)


def contar(texto) -> int:
    if not texto:
        return 0
    return len(PALABRA.findall(str(texto)))


def _rango(cfg, clave):
    r = cfg.get("rangos_palabras", {}).get(clave, {})
    return r.get("min"), r.get("max")


def _chk_rango(hallazgos, cfg, clave, ruta, texto, severidad="error"):
    mn, mx = _rango(cfg, clave)
    n = contar(texto)
    if mn is not None and n < mn:
        hallazgos.append({
            "severidad": severidad, "ruta": ruta, "regla": f"{clave} ≥ {mn} palabras",
            "detalle": f"tiene {n}, faltan {mn - n}", "valor": n, "min": mn, "max": mx,
        })
    elif mx is not None and n > mx:
        hallazgos.append({
            "severidad": severidad, "ruta": ruta, "regla": f"{clave} ≤ {mx} palabras",
            "detalle": f"tiene {n}, sobran {n - mx}", "valor": n, "min": mn, "max": mx,
        })
    return n


def _similares(a: str, b: str) -> float:
    return SequenceMatcher(None, a[:600], b[:600]).ratio()


# --------------------------------------------------------------------------

def validar_modulo(m: dict, cfg: dict, syllabus: dict | None = None) -> list[dict]:
    h: list[dict] = []
    num = m.get("numero", "?")

    _chk_rango(h, cfg, "introduccion_modulo", f"M{num}.introduccion", m.get("introduccion"))
    _chk_rango(h, cfg, "cierre_modulo", f"M{num}.cierre.texto",
               (m.get("cierre") or {}).get("texto"))

    foro = m.get("foro") or {}
    if not re.search(r"comenta", foro.get("instruccion", ""), re.I):
        h.append({"severidad": "error", "ruta": f"M{num}.foro.instruccion",
                  "regla": "el foro debe pedir comentar a otro participante",
                  "detalle": "no se encontró la instrucción de comentar"})

    ids_validos = set()
    if syllabus:
        for sm in syllabus.get("modulos", []):
            for r in sm.get("bibliografia_recursos", []):
                ids_validos.add(r)

    practicas_finales = 0
    textos_previos: list[tuple[str, str]] = []

    for t in m.get("temas", []):
        tn = t.get("numero", "?")
        base = f"M{num}.T{tn}"

        _chk_rango(h, cfg, "introduccion_tema", f"{base}.introduccion", t.get("introduccion"))

        cb = t.get("conceptos_basicos") or {}
        _chk_rango(h, cfg, "conceptos_basicos", f"{base}.conceptos_basicos.nucleo", cb.get("nucleo"))
        if cb.get("profundiza"):
            _chk_rango(h, cfg, "profundiza", f"{base}.conceptos_basicos.profundiza",
                       cb.get("profundiza"), severidad="aviso")

        est = cfg.get("estructura_conceptos_basicos", {})
        nclave = len(cb.get("conceptos_clave") or [])
        if nclave < est.get("conceptos_clave_min", 6):
            h.append({"severidad": "error", "ruta": f"{base}.conceptos_clave",
                      "regla": f"≥ {est.get('conceptos_clave_min', 6)} conceptos clave",
                      "detalle": f"tiene {nclave}"})
        if nclave > est.get("conceptos_clave_max", 10):
            h.append({"severidad": "aviso", "ruta": f"{base}.conceptos_clave",
                      "regla": f"≤ {est.get('conceptos_clave_max', 10)} conceptos clave",
                      "detalle": f"tiene {nclave}"})

        if est.get("diagrama_obligatorio") and not (cb.get("diagrama") or {}).get("descripcion"):
            h.append({"severidad": "error", "ruta": f"{base}.conceptos_basicos.diagrama",
                      "regla": "diagrama obligatorio", "detalle": "falta la descripción"})

        nej = len(cb.get("ejemplos") or [])
        if nej < est.get("ejemplos_min", 2):
            h.append({"severidad": "error", "ruta": f"{base}.ejemplos",
                      "regla": f"≥ {est.get('ejemplos_min', 2)} ejemplos", "detalle": f"tiene {nej}"})

        prev = t.get("preguntas_previas") or []
        esperadas = cfg.get("recuperacion_activa", {}).get("preguntas_previas_por_tema", 2)
        if len(prev) != esperadas:
            h.append({"severidad": "aviso", "ruta": f"{base}.preguntas_previas",
                      "regla": f"{esperadas} preguntas de recuperación activa",
                      "detalle": f"tiene {len(prev)}"})

        co = t.get("conecta") or {}
        _chk_rango(h, cfg, "conecta", f"{base}.conecta.texto", co.get("texto"))
        if not co.get("frase_memorable"):
            h.append({"severidad": "error", "ruta": f"{base}.conecta.frase_memorable",
                      "regla": "frase memorable obligatoria", "detalle": "está vacía"})

        pr = t.get("practica") or {}
        _chk_rango(h, cfg, "practica", f"{base}.practica.instrucciones", pr.get("instrucciones"))
        min_det = cfg.get("practica", {}).get("preguntas_detonantes_min", 3)
        ndet = len(pr.get("preguntas_detonantes") or [])
        if ndet < min_det:
            h.append({"severidad": "error", "ruta": f"{base}.practica.preguntas_detonantes",
                      "regla": f"≥ {min_det} preguntas detonantes", "detalle": f"tiene {ndet}"})
        if pr.get("cuenta_para_evaluacion_final"):
            practicas_finales += 1

        ad = t.get("adopta") or {}
        _chk_rango(h, cfg, "adopta", f"{base}.adopta.texto", ad.get("texto"))
        if not ad.get("takeaway"):
            h.append({"severidad": "error", "ruta": f"{base}.adopta.takeaway",
                      "regla": "takeaway obligatorio", "detalle": "está vacío"})

        if ids_validos:
            for c in cb.get("citas", []):
                if c not in ids_validos:
                    h.append({"severidad": "aviso", "ruta": f"{base}.citas",
                              "regla": "las citas deben existir en el syllabus",
                              "detalle": f"'{c}' no aparece en la bibliografía del syllabus"})

        for etiqueta, txt in (("conceptos", cb.get("nucleo")), ("conecta", co.get("texto")),
                              ("adopta", ad.get("texto"))):
            if not txt:
                continue
            for et_prev, t_prev in textos_previos:
                if _similares(str(txt), str(t_prev)) > 0.85:
                    h.append({"severidad": "aviso", "ruta": f"{base}.{etiqueta}",
                              "regla": "sin texto repetido entre secciones",
                              "detalle": f"muy parecido a {et_prev}"})
            textos_previos.append((f"{base}.{etiqueta}", str(txt)))

    esperadas = cfg.get("practica", {}).get("practicas_que_cuentan_para_evaluacion_final_por_modulo", 1)
    if m.get("temas") and practicas_finales != esperadas:
        h.append({"severidad": "aviso", "ruta": f"M{num}.practicas",
                  "regla": f"{esperadas} práctica marcada para la evaluación final",
                  "detalle": f"hay {practicas_finales}"})

    return h


def validar_evaluaciones(ev: dict, cfg: dict) -> list[dict]:
    h: list[dict] = []
    c = cfg.get("evaluacion", {})

    def _banco(preguntas, ruta, esperadas):
        if len(preguntas) != esperadas:
            h.append({"severidad": "error", "ruta": ruta,
                      "regla": f"{esperadas} preguntas", "detalle": f"tiene {len(preguntas)}"})

        letras, largas = [], 0
        for p in preguntas:
            pid = p.get("id", "?")
            ops = p.get("opciones") or []
            if len(ops) != c.get("opciones_por_pregunta", 4):
                h.append({"severidad": "error", "ruta": f"{ruta}.{pid}",
                          "regla": "4 opciones", "detalle": f"tiene {len(ops)}"})
            correctas = [o for o in ops if o.get("error_conceptual") in (None, "")]
            if len(correctas) != 1:
                h.append({"severidad": "error", "ruta": f"{ruta}.{pid}",
                          "regla": "exactamente 1 opción correcta",
                          "detalle": f"hay {len(correctas)} sin error_conceptual"})
            elif correctas[0].get("letra") != p.get("respuesta_correcta"):
                h.append({"severidad": "error", "ruta": f"{ruta}.{pid}",
                          "regla": "la correcta coincide con respuesta_correcta",
                          "detalle": f"{correctas[0].get('letra')} vs {p.get('respuesta_correcta')}"})
            letras.append(p.get("respuesta_correcta"))
            if ops and correctas:
                if len(correctas[0].get("texto", "")) == max(len(o.get("texto", "")) for o in ops):
                    largas += 1
            for o in ops:
                if re.search(r"todas las anteriores|ninguna de las anteriores|\bA y B\b",
                             o.get("texto", ""), re.I):
                    h.append({"severidad": "error", "ruta": f"{ruta}.{pid}",
                              "regla": "sin opciones comodín",
                              "detalle": f"'{o.get('texto')[:50]}'"})
            if not p.get("explicacion"):
                h.append({"severidad": "error", "ruta": f"{ruta}.{pid}",
                          "regla": "explicación obligatoria", "detalle": "está vacía"})

        if preguntas:
            for L in "ABCD":
                pct = letras.count(L) / len(preguntas)
                if pct > 0.40:
                    h.append({"severidad": "aviso", "ruta": ruta,
                              "regla": "ninguna letra concentra más del 40% de las correctas",
                              "detalle": f"la {L} tiene {pct:.0%}"})
            if largas / len(preguntas) > 0.40:
                h.append({"severidad": "aviso", "ruta": ruta,
                          "regla": "la correcta no es la más larga en >40%",
                          "detalle": f"lo es en {largas / len(preguntas):.0%}"})

    diag = ev.get("diagnostica") or {}
    _banco(diag.get("preguntas") or [], "diagnostica", c.get("diagnostica_num_preguntas", 10))

    for banco in ev.get("por_modulo", []):
        ruta = f"modulo_{banco.get('modulo')}"
        preguntas = banco.get("preguntas") or []
        _banco(preguntas, ruta, c.get("por_modulo_num_preguntas", 5))
        niveles = [p.get("nivel_cognitivo") for p in preguntas]
        altos = sum(1 for n in niveles if n in ("aplicacion", "analisis"))
        if preguntas and altos < 3:
            h.append({"severidad": "aviso", "ruta": ruta,
                      "regla": "≥3 de 5 preguntas en nivel aplicación o análisis",
                      "detalle": f"hay {altos}"})
    return h


def validar_marco(marco: dict, cfg: dict) -> list[dict]:
    h: list[dict] = []
    fac = marco.get("facilitador") or {}
    bullets = fac.get("bullets") or []
    if len(bullets) != 6:
        h.append({"severidad": "error", "ruta": "facilitador.bullets",
                  "regla": "exactamente 6 bullets", "detalle": f"tiene {len(bullets)}"})

    it = marco.get("importancia_tema") or {}
    for k in ("contras_de_no_aprender", "pros_de_aprender"):
        if len(it.get(k) or []) != 2:
            h.append({"severidad": "error", "ruta": f"importancia_tema.{k}",
                      "regla": "exactamente 2", "detalle": f"tiene {len(it.get(k) or [])}"})
    if contar(it.get("takeaway")) > 15:
        h.append({"severidad": "aviso", "ruta": "importancia_tema.takeaway",
                  "regla": "máximo 15 palabras", "detalle": f"tiene {contar(it.get('takeaway'))}"})

    enc = marco.get("encuesta_satisfaccion") or {}
    for k in ("preguntas_curso", "preguntas_instructor"):
        n = len(enc.get(k) or [])
        if n != 5:
            h.append({"severidad": "error", "ruta": f"encuesta_satisfaccion.{k}",
                      "regla": "5 preguntas", "detalle": f"tiene {n}"})

    req = (marco.get("requisitos") or {}).get("resumen", "") + " ".join(
        str(v) for v in (marco.get("requisitos") or {}).values())
    a = cfg.get("acreditacion", {})
    if str(a.get("asistencia_minima_pct", 80)) not in req:
        h.append({"severidad": "aviso", "ruta": "requisitos",
                  "regla": "los requisitos citan los porcentajes del config",
                  "detalle": f"no se encontró {a.get('asistencia_minima_pct')}%"})

    texto = str(marco)
    if "[POR DEFINIR]" in texto and not marco.get("alertas"):
        h.append({"severidad": "aviso", "ruta": "alertas",
                  "regla": "los [POR DEFINIR] se declaran en alertas",
                  "detalle": "hay campos sin dato y `alertas` está vacío"})
    return h


def validar_presentacion(pres: dict, cfg: dict, horas_curso: float) -> list[dict]:
    h: list[dict] = []
    modalidad = pres.get("modalidad", "")
    conf = cfg.get("presentaciones", {}).get("modalidades", {}).get(modalidad, {})
    slides = pres.get("slides") or []

    max_b = cfg.get("presentaciones", {}).get("bullets_por_slide_max", 5)
    ppm = cfg.get("presentaciones", {}).get("palabras_por_minuto_script", 150)

    for s in slides:
        n = s.get("numero", "?")
        bullets = (s.get("contenido") or {}).get("bullets") or []
        if len(bullets) > max_b:
            h.append({"severidad": "error", "ruta": f"slide {n}",
                      "regla": f"≤ {max_b} bullets", "detalle": f"tiene {len(bullets)}"})
        if len(str(s.get("imagen_o_diagrama", ""))) < 60:
            h.append({"severidad": "aviso", "ruta": f"slide {n}",
                      "regla": "la sugerencia visual debe ser accionable (≥60 caracteres)",
                      "detalle": f"tiene {len(str(s.get('imagen_o_diagrama', '')))}"})
        if conf.get("lleva_duracion") and s.get("duracion_min"):
            esperado = float(s["duracion_min"]) * ppm
            real = contar(s.get("script"))
            if real < esperado * 0.6 or real > esperado * 1.4:
                h.append({"severidad": "aviso", "ruta": f"slide {n}.script",
                          "regla": f"~{ppm} palabras por minuto",
                          "detalle": f"{real} palabras para {s['duracion_min']} min"})
        if not conf.get("lleva_duracion") and s.get("duracion_min") is not None:
            h.append({"severidad": "aviso", "ruta": f"slide {n}.duracion_min",
                      "regla": "la modalidad asíncrona no lleva duración por slide",
                      "detalle": "viene con valor"})

    tipos = [s.get("tipo") for s in slides]
    for obligatorio in ("caratula_curso", "cierre_curso"):
        if tipos.count(obligatorio) != 1:
            h.append({"severidad": "error", "ruta": "estructura",
                      "regla": f"exactamente un slide '{obligatorio}'",
                      "detalle": f"hay {tipos.count(obligatorio)}"})

    if conf.get("lleva_minuto_a_minuto") and not pres.get("minuto_a_minuto"):
        h.append({"severidad": "error", "ruta": "minuto_a_minuto",
                  "regla": "obligatorio en modalidad en sitio y virtual", "detalle": "falta"})

    if conf.get("lleva_duracion"):
        total = sum(float(s.get("duracion_min") or 0) for s in slides)
        objetivo = horas_curso * 60
        if objetivo and abs(total - objetivo) / objetivo > 0.15:
            h.append({"severidad": "aviso", "ruta": "duracion_total_min",
                      "regla": f"±15% de {objetivo:.0f} min",
                      "detalle": f"suma {total:.0f} min"})
    return h


def resumen(hallazgos: list[dict]) -> dict:
    errores = [x for x in hallazgos if x["severidad"] == "error"]
    avisos = [x for x in hallazgos if x["severidad"] == "aviso"]
    return {"pasa": not errores, "errores": len(errores), "avisos": len(avisos),
            "hallazgos": hallazgos}
