# Documentación del dominio

Cómo las skills de ingeniería leen el vocabulario y las decisiones de este repo.

## Antes de explorar, lee

- **`GLOSSARY.md`** en la raíz.
- **`docs/adr/`**: los ADR que toquen el área en la que vas a trabajar.

Si no existen, sigue sin mencionarlo. Los crea `/domain-modeling` (a través de
`/grill-with-docs` y `/improve-codebase-architecture`) cuando un término o una decisión
se resuelve de verdad.

## Usa el vocabulario del glosario

Cuando nombres un concepto del dominio (en un issue, una propuesta, una hipótesis, el
nombre de una prueba), usa el término como está en `GLOSSARY.md`. Si el concepto no está,
o estás inventando lenguaje que el proyecto no usa, o falta un término: anótalo para
`/domain-modeling`.

## Señala los choques con un ADR

Si lo que propones contradice un ADR, dilo explícitamente: _"Contradice el ADR-0003, pero
vale la pena reabrirlo porque..."_.
