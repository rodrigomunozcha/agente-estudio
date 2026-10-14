# Agente estudio

Convierte grabaciones de clase en material de estudio sin supervisión, y atiende al
estudiante por Telegram como secretaria y profe de cada ramo. Es una copia local de
`orquestador-estudio`, hecha el 29-09-2026 y sin remote. El original sigue intacto en
`../orquestador-estudio`. El diseño y sus decisiones están en `PROMPT-inicial.md`,
que vive solo en este Mac porque trae el horario y las fechas reales del estudiante.

Aquí van las reglas que valen para todo el repositorio. Las reglas de la corrida
automatizada, que son de detalle fino y solo aplican al pipeline, están en
`orquestador/CLAUDE.md`.

## Español neutro con tuteo, siempre

Vale para todo: la conversación, el código, los comentarios, la documentación, los
commits y los textos que el sistema muestra. Tú tienes, puedes, revisa, dime. Sin
regionalismos de ningún país. Sin em-dash ni punto y coma: usa " - " o parte la frase.
Los conceptos que en el área se usan en inglés quedan en inglés, dentro de una frase en
español: brand awareness, customer journey, insight.

Repite la regla 1 de `~/.claude/CLAUDE.md` a propósito: la skill del pipeline carga este
archivo y no el global.

## Antes de cambiar código o investigar un fallo

Lee `docs/ARQUITECTURA.md`: comandos (pruebas, salud del sistema, ensayo, reiniciar el
bot), los LaunchAgents, el camino de una clase, los archivos de estado y las piezas que
cruzan todo el sistema. Lo de desarrollo vive allá porque este archivo entra en cada
llamada del pipeline a la skill.

## Estilo y claridad

- Los comentarios explican **por qué**, no qué. El estilo del repo es dejar dicho qué se
  probó y qué falló, para que nadie lo vuelva a intentar. Ver `skill_runner.py`, donde
  cada decisión trae la razón y lo verificado en vivo. Mantén ese nivel.
- **Esta regla gana sobre el comportamiento por defecto de no comentar.** Los bloques
  largos de comentario de este repo son deliberados: son el registro de lo que ya se
  intentó. No los recortes por prolijidad ni los conviertas en una línea.
- Cada mensaje que ve el usuario dice qué hace y qué va a pasar. No des el contexto por
  supuesto: quien usa esto no tiene la terminal abierta.

## Privacidad: qué nunca sale de este Mac

**La fuente de verdad es `.gitignore`**, y cada exclusión trae escrito ahí su motivo. No
repitas esa lista en otro lado: dos listas se separan y una queda mintiendo.

Lo que protege, en general: grabaciones y notas de clases reales, material que deriva de
los apuntes de cada profesor, rutas propias de este Mac, y archivos de estado que traen
fragmentos de transcripción.

Este repo es público. Un comentario, una prueba o un ejemplo que recuerda un caso real lo
cita por su fecha ("la clase del 08-10-2026"), nunca por el ramo, la persona, la empresa
o la sala: el 14-10-2026 llegaron a GitHub nombres de ramos escritos así. El hook
compartido frena esos nombres al hacer commit y audita cada push, pero un dato sin forma
fija, como la descripción de alguien, solo lo ves tú.

Dos reglas que se siguen de eso:

- Antes de cualquier commit, confirma que `git status` no menciona nada ignorado.
- Si agregas una carpeta que va a guardar material de clases, estado de corrida o rutas
  personales, agrégala a `.gitignore` en el mismo cambio, con el porqué escrito.

## Antes de cada commit

**Nunca uses `git add -A` ni `git add .`.** Agrega archivos por nombre, uno por uno.
Un `git add -A` metió 848 líneas de trabajo de un día entero (el respaldo del título, el
gate de rutas, el manejo de errores del SDK, el veredicto de la revisión, las pruebas)
dentro de un commit que solo hablaba de fechas de archivo (`911e2c6`, 21-08-2026).
Nadie lo notó porque nadie miró la lista de archivos antes de confirmar.

Por eso hay un hook de pre-commit versionado en `.githooks/pre-commit`: antes de cada
commit muestra los archivos en stage agrupados por carpeta y exige una confirmación
explícita. No intenta adivinar si un commit mezcla temas por su tamaño: se probó contra
los commits reales de este repo y el commit problemático (13 archivos, 1039 líneas) es
más chico que dos commits legítimos de un solo tema (18 archivos/1901 líneas, y 16
archivos/1141 líneas). El tamaño no es una señal confiable. Lo único que el hook
garantiza es que alguien mire antes de confirmar.

Para activarlo, una vez por clon del repositorio:

```bash
git config core.hooksPath .githooks
```

Sin activar, el hook no hace nada: es opt-in porque tocar `core.hooksPath` es una
decisión de quien clona, no algo que deba imponerse solo.

## Entorno

- **Python 3.13** del instalador oficial, en
  `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`. No el de macOS.
- **Node 22+**, con el CLI de Claude Code en `node_modules/.bin/claude`.
- Mac con Apple Silicon. Probado en vivo en un M3.
- Las apps de `boton_app/` se compilan en cada Mac con `bash boton_app/compilar_apps.sh`.
  Los `.app` no se suben: macOS los pondría en cuarentena y un binario no se puede
  auditar. Lo que se publica es el `.applescript`. Mismo criterio para el binario de
  `barra_menu/`.

## Skills de ingeniería

- Tickets: GitHub Issues de este repo, que es público. Ver `docs/agents/issue-tracker.md`.
- Etiquetas de triage: las cinco por defecto. Ver `docs/agents/triage-labels.md`.
- Documentación del dominio: un solo contexto (`GLOSSARY.md` y `docs/adr/`). Ver `docs/agents/domain.md`.
