# Estándares de código

Reglas de criterio para la revisión de código (`/code-review`). Son las que ninguna
herramienta puede comprobar sola. Lo mecánico vive en `.githooks/pre-commit`. Las
reglas de idioma, privacidad y estilo de comentarios están en `CLAUDE.md` y también
valen aquí.

Este archivo no entra en las llamadas del pipeline, a diferencia del `CLAUDE.md`, así
que una regla para el revisor va aquí y no allá.

## Todo error que puede perder material del estudiante queda registrado

Una grabación, una pista, una nota o una transcripción que no se pudo escribir deja
una línea en el registro con el motivo, y si el estudiante tiene que actuar, un aviso.
Revisa cada `try?` en Swift, cada `except` que no hace nada en Python y cada
`|| true` en bash que rodeen una escritura de esos datos.

Por qué: en la grabadora de reuniones, tres fallas distintas quedaron tapadas por un
`try?` (la tasa de bits de AAC, el micrófono de 3 canales y el archivo de un tramo que
no se creaba). Cada una costó una prueba en vivo con el estudiante, y las tres se
habrían visto en la primera si hubieran dejado una línea en el registro (15-10-2026).

## Un comentario afirma una causa solo si se verificó

"Falla porque X" va acompañado de cómo se comprobó: una línea del registro, una
reproducción o una medición. Una causa sin comprobar se escribe como sospecha ("se
sospecha que X, sin verificar"). Cuando una sospecha resulta falsa, el comentario dice
qué se creyó y qué era en realidad, en vez de borrarla: así nadie vuelve a seguirla.

Por qué: en la grabadora quedaron escritos como hechos dos diagnósticos equivocados (un
segundo de espera que no alcanzaba y una excepción por formato). Los dos guiaron
correcciones que no arreglaron nada, hasta que el registro del sistema mostró la causa
real (15-10-2026).
