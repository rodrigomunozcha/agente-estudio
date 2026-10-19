# Agente estudio

Convierte grabaciones de clase en material de estudio sin supervisión, y atiende al
estudiante por Telegram como secretaria y profe de cada ramo. Es el repositorio público
`rodrigomunozcha/agente-estudio`. El diseño y sus decisiones están en `PROMPT-inicial.md`,
que vive solo en este Mac porque trae el horario y las fechas reales del estudiante.

Este archivo entra en cada llamada del pipeline a la skill, así que aquí van solo las
reglas que valen para todo el repositorio. Las de la corrida automatizada están en
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
cruzan todo el sistema. Las reglas de criterio para revisar un cambio están en
`CODING_STANDARDS.md`. Python siempre es el 3.13 del instalador oficial,
`/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`, no el de macOS.

## Estilo y claridad

- Los comentarios explican **por qué**, no qué. El estilo del repo es dejar dicho qué se
  probó y qué falló, para que nadie lo vuelva a intentar. Ver `skill_runner.py`, donde
  cada decisión trae la razón y lo verificado en vivo. Mantén ese nivel.
- **Esta regla gana sobre el comportamiento por defecto de no comentar.** Los bloques
  largos de comentario de este repo son deliberados: son el registro de lo que ya se
  intentó. Consérvalos completos.
- Cada mensaje que ve el usuario dice qué hace y qué va a pasar. Quien usa esto no tiene
  la terminal abierta.

## Privacidad: qué nunca sale de este Mac

**La fuente de verdad es `.gitignore`**, y cada exclusión trae escrito ahí su motivo. No
repitas esa lista en otro lado: dos listas se separan y una queda mintiendo.

Lo que protege, en general: grabaciones y notas de clases reales, material que deriva de
los apuntes de cada profesor, rutas propias de este Mac, y archivos de estado que traen
fragmentos de transcripción.

Un comentario, una prueba o un ejemplo que recuerda un caso real lo cita por su fecha
("la clase del 08-10-2026"), nunca por el ramo, la persona, la empresa o la sala: el
14-10-2026 llegaron a GitHub nombres de ramos escritos así. El hook compartido frena esos
nombres al hacer commit y audita cada push, pero un dato sin forma fija, como la
descripción de alguien, solo lo ves tú.

Si agregas una carpeta que va a guardar material de clases, estado de corrida o rutas
personales, agrégala a `.gitignore` en el mismo cambio, con el porqué escrito.

## Antes de cada commit

Agrega los archivos por nombre, uno por uno, y confirma que `git status` no menciona nada
ignorado. El hook propio (`.githooks/pre-commit`, con su historia en el encabezado)
muestra lo que va en stage y exige `CONFIRMO_COMMIT=1`: mira la lista antes de confirmar.

El repo está conectado al hook compartido, que corre la auditoría de privacidad y después
el de `.githooks/`. En un clon nuevo se conecta así:

```bash
git config core.hooksPath "$HOME/Documents/Claude Code/_herramientas/git-hooks"
```

Apuntar `core.hooksPath` a `.githooks` desconecta la auditoría de privacidad.

## Skills de ingeniería

- Tickets: GitHub Issues de este repo, que es público. Ver `docs/agents/issue-tracker.md`.
- Etiquetas de triage: las cinco por defecto. Ver `docs/agents/triage-labels.md`.
- Documentación del dominio: un solo contexto (`GLOSSARY.md` y `docs/adr/`). Ver `docs/agents/domain.md`.
