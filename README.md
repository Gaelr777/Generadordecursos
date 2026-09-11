# Generador de Cursos — Club de Mentes Brillantes

Herramienta interna. Subes la ficha técnica de un curso y devuelve todo el contenido:
syllabus, módulos, marco, evaluaciones, material para video y presentaciones.

Alineada al **Playbook para creación de cursos en Mentes Brillantes v3.0**.

---

## Instalación

Necesitas Python 3.10 o superior.

**macOS / Linux**

```bash
./run.sh
```

**Windows**

```
run.bat
```

La primera vez crea el entorno virtual, instala dependencias y genera `.env`. Abre ese
archivo, pon tu `ANTHROPIC_API_KEY`, y vuelve a correr. Después abre
**http://127.0.0.1:8000** en el navegador.

La API key vive solo en tu `.env`. La ficha se procesa en tu computadora; lo único que sale
es la llamada a la API para generar el contenido.

---

## Cómo se usa

1. **Ficha técnica.** Arrastra el `.docx`, `.pdf`, `.txt` o `.md`. La herramienta extrae el
   texto y las tablas y te muestra cuántas palabras leyó, para que confirmes que el
   documento se leyó completo antes de gastar una generación.

2. **Qué generar.** Eliges modelo, facilitador y piezas. Los módulos siempre se generan; el
   marco, las evaluaciones, el material de video y las presentaciones son opcionales.
   Las tres modalidades de presentación corresponden a los Prompts 7, 8 y 9 del playbook.

3. **Generación.** Verás el avance etapa por etapa. Ficha y syllabus corren en secuencia
   porque cada uno depende del anterior; todo lo demás se lanza en paralelo.

4. **Revisión.** Cada sección aparece con su conteo de palabras contra el rango permitido, y
   con las verificaciones que pasó o falló. Las notas del área académica (cosas que el
   generador dedujo y alguien debe validar) salen en su propia pestaña. De ahí descargas
   el `.zip` con todos los Word y los JSON.

---

## Mapeo contra el playbook v3

| Etapa | Playbook v3 | Produce |
|---|---|---|
| E1 | Prompt 1 | `ficha.json` |
| E2 | Prompt 2 | `syllabus.json` |
| E3 | Prompt 3 | `modulo_N.json` — una llamada por módulo, en paralelo |
| E4 | Prompt 4 | `marco.json` — bienvenida, requisitos, facilitador, cierre, insignia |
| E5 | Prompt 5 | `evaluaciones.json` — diagnóstica de 10 + 5 por módulo, con GIFT |
| E6 | Prompt 6 | `video_N.json` — outline de 15 min por módulo |
| E7 | Prompts 7 / 8 / 9 | `presentacion_{sitio\|virtual\|asincrona}.json` |

### Diferencias respecto al v1.0 que se aplicaron

- **Renumeración completa.** Los prompts 3 a 9 se recorrieron. El antiguo Prompt 4
  (contenido de módulo) es ahora el 3; el 5 es el 4; el 7 es el 5; el 8 es el 6.
- **Las presentaciones se separaron en tres.** Antes eran un prompt repetido tres veces con
  variaciones; ahora son sitio (1 práctica por tema, con duración), virtual (2 prácticas,
  con duración, herramientas colaborativas) y asíncrona (2 prácticas, **sin** columna de
  duración). El generador respeta esa diferencia y el validador la verifica.
- **La insignia se movió** del prompt de evaluación al del marco del curso, que es donde
  corresponde.
- **Conceptos Básicos bajó de 350 a 300 palabras** como mínimo.
- **La práctica ahora exige al menos 3 preguntas detonantes** y una práctica por módulo
  marcada como parte de la evaluación final.
- **Tabla de minuto a minuto** en las presentaciones en sitio y virtual.

### Cambios propios, no del playbook

- **Conceptos Básicos dejó de ser prosa corrida.** Es la sección a la que el participante
  regresa a buscar algo concreto, así que se reestructuró: núcleo de 250–300 palabras +
  tabla de conceptos clave + diagrama + ejemplos + un bloque `profundiza` colapsable. La
  lectura de primera pasada queda en ~300 palabras y el total del bloque sigue por encima
  del mínimo del playbook.
- **Rangos con techo.** El playbook solo fija mínimos, y con solo mínimo la generación se
  disparaba al doble. Ahora cada sección tiene mínimo y máximo, y pasarse cuenta como error.
- **Recuperación activa.** Dos preguntas por tema antes de Conceptos Básicos, sin
  calificación. Intentar responder antes de leer mejora la retención más que una
  explicación más larga.
- **Bibliografía de fuentes abiertas.** El Paso 1 del playbook indica descargar de Anna's
  Archive. Eso se sustituyó por fuentes con licencia abierta, estándares gratuitos y
  material adquirido; la lista de repositorios vetados está en el config y el generador la
  respeta.

---

## Estructura

```
generador-cursos-mb/
├── app.py                  servidor FastAPI, endpoints y estado de trabajos
├── pipeline/
│   ├── prompts.py          los 7 prompts + mapeo al playbook
│   ├── extract.py          .docx / .pdf / .txt → texto
│   ├── generate.py         llamadas al modelo, caché, paralelismo, reintentos
│   ├── validate.py         verificaciones por código
│   └── render.py           JSON → Word y empaquetado .zip
├── config/
│   ├── curso.config.json   constantes institucionales y rangos
│   └── design-system.json  paleta y taxonomía de slides
└── static/index.html       la interfaz
```

### Dónde tocar cada cosa

| Quieres cambiar… | Archivo |
|---|---|
| Criterios de acreditación, rangos de palabras, límites de estructura | `config/curso.config.json` |
| Cómo se escribe el contenido (tono, estructura, reglas) | `pipeline/prompts.py` |
| Qué se verifica antes de aprobar | `pipeline/validate.py` |
| Cómo se ve el Word | `pipeline/render.py` |
| Paleta y tipos de slide | `config/design-system.json` |

Los criterios institucionales viven **solo** en `curso.config.json`. No los repitas dentro
de un prompt: se inyecta completo en cada llamada.

---

## Costo

Tres cosas lo sostienen:

- **Caché de prompt.** El bloque de sistema es idéntico entre módulos y va marcado para
  cachearse: se cobra completo una vez y con descuento en las llamadas restantes. La
  pantalla de revisión muestra cuántos tokens se leyeron desde caché.
- **Paralelismo sin estado.** Ninguna llamada arrastra el contexto de las otras. Un curso de
  tres módulos con marco, evaluaciones y una presentación son seis llamadas simultáneas.
- **Reintento por etapa.** Si el módulo 2 falla la validación, se regenera el módulo 2 con
  los errores concretos como contexto — no el curso completo. Máximo dos reintentos.

---

## Pendientes conocidos

- **Moodle.** Fuera de alcance por ahora. Cuando se defina el hosting, el paso siguiente es
  empaquetar a `.mbz` y restaurarlo por CLI, que es la única vía que cubre páginas,
  cuestionarios, tareas y foros sin intervención humana.
- **Los diagramas se describen, no se dibujan.** Cada sección de Conceptos Básicos entrega
  la descripción del diagrama y el Word deja el espacio reservado.
- **Sin historial.** El estado vive en memoria: al reiniciar el servidor se pierde. Los
  `.zip` descargados quedan en `salidas/`.
- **Un curso a la vez.** Es una herramienta local de un usuario; no hay cola ni concurrencia.
