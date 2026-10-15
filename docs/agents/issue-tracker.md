# Tickets: GitHub Issues

Los issues y las especificaciones de este repo viven en sus GitHub Issues. Todo se hace
con `gh`, que deduce el repo de `git remote -v` cuando corre dentro del clon.

## Este repo es público, y sus issues también

Un issue nunca lleva datos personales: nombres reales de ramos, profesores, personas o
empresas, fechas reales de clases o evaluaciones, horarios, salas, ni rutas de este Mac.
Escribe "un ramo", "una entrevista", "la clase del día X", con datos ficticios. Antes
de crear o comentar un issue, revisa su texto contra esta regla. Un texto publicado
queda en el historial de GitHub aunque después se edite.

## Comandos

- **Crear**: `gh issue create --title "..." --body "..."`. Para varias líneas, un heredoc.
- **Leer**: `gh issue view <número> --json number,title,body,labels,comments`.
- **Listar**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`, con `--label` y `--state` según haga falta.
- **Sub-issue de un padre**: `gh issue create --parent <padre> ...`, o `gh issue edit <padre> --add-sub-issue <hijo>`. Sin sub-issues, `Part of #<padre>` al inicio del cuerpo del hijo.
- **Comentar**: `gh issue comment <número> --body "..."`.
- **Etiquetas**: `gh issue edit <número> --add-label "..."` y `--remove-label "..."`.
- **Cerrar**: un ticket resuelto por un commit se cierra con una línea `Closes #<número>` al
  final del mensaje del commit. GitHub lo cierra cuando el commit llega, así que un push
  frenado no deja un issue cerrado con un commit que no existe allá (pasó el 15-10-2026).
  GitHub no reconoce "Resuelve". Solo lo que no sale de un commit se cierra a mano:
  `gh issue close <número> --comment "..."`.

## Pull requests como pedidos

**Los PR cuentan como pedidos: no.** _(Cambia a `yes` si los PR externos se tratan como
pedidos de feature. Lo lee `/triage`.)_

## Cuando una skill dice "publicar en el tracker"

Crea un issue de GitHub.

## Cuando una skill dice "traer el ticket"

Léelo como en **Leer**.

## Wayfinder

Lo usa `/wayfinder`. El **mapa** es un issue con etiqueta `wayfinder:map`, y sus tickets
son sub-issues con etiqueta `wayfinder:<tipo>` (`research`, `prototype`, `grilling`, `task`).

- **Bloqueos**: las dependencias nativas de GitHub. `gh api --method POST repos/<owner>/<repo>/issues/<hijo>/dependencies/blocked_by -F issue_id=<id-de-base-de-datos-del-bloqueador>`, con el id de `gh api repos/<owner>/<repo>/issues/<n> --jq .id` (no el `#número`). Sin dependencias, una línea `Blocked by: #<n>` al inicio del cuerpo.
- **Frontera**: los hijos abiertos del mapa, sin bloqueadores abiertos ni asignado. Gana el primero en el orden del mapa.
- **Tomar**: `gh issue edit <n> --add-assignee @me`.
- **Resolver**: comentar la respuesta, cerrar, y agregar un puntero al mapa.
