# Agente estudio

Grabas la clase con el iPhone, la mandas al Mac por AirDrop y no haces nada más. El agente
la transcribe, escribe tus apuntes en Obsidian, revisa su propio trabajo, arma una hoja
de repaso en HTML y la deja en OneDrive. Además, mantiene al día una hoja por cada
solemne y examen, y te atiende por Telegram como secretaria y como profe de cada ramo.
Todo corre en tu propio Mac con tu plan Claude Pro, sin pagos extra.

Nació el 29-09-2026 como copia de `orquestador-estudio`, que sigue intacto en
`../orquestador-estudio` y es el repositorio público.

## Qué hace

| Pieza | Qué te entrega |
|---|---|
| Clase por AirDrop | El audio llega a Descargas y el agente lo procesa solo, sin clics |
| Apuntes | Notas de Obsidian por clase: ideas centrales, preguntas con respuesta modelo, kit de repaso |
| Hoja de la clase | Una hoja de repaso en HTML, enviada a Telegram y guardada junto al audio y la transcripción en la carpeta Clases del ramo, en OneDrive |
| Hoja de la evaluación | Una hoja por solemne o examen que junta las clases que entran, y se rehace con cada clase nueva |
| Anuncios del profesor | Controles, tareas y fechas de evaluación que dijo en clase, anotados solo si la cita está en el audio |
| Secretaria de las 9:00 | Lo que pidió el profesor para hoy, las evaluaciones de la semana, y cada solemne o control dos días antes, la víspera y el mismo día |
| Bot de Telegram | Responde dudas del ramo con tus apuntes como respaldo, conoce tu horario y tus evaluaciones, y guarda los archivos que le mandes |
| Reuniones y entrevistas | Resumen, decisiones, acciones y temas para investigar, en Obsidian, en un .docx y con un botón para Google Calendar |

## Uso diario

- **Una clase:** mándala por AirDrop. Te llega por Telegram cuando termina, en unos 25 a 45 minutos.
- **Una clase sin AirDrop:** déjala en `Input/`, o guárdala desde el teléfono en la carpeta de
  OneDrive de `rutas.grabaciones_onedrive`. Se procesa sola cuando el Mac está encendido. El
  original de OneDrive pasa a `~/Documents/Claude Code/archivos para borrar/`, la carpeta común
  de todo lo que puedes borrar, igual que los audios originales sin comprimir.
- **Si el nombre no dice de qué es:** te pregunto por Telegram, con un botón por ramo, `Reunión` y
  `No es una clase`. La grabación espera tu respuesta, y si no contestas te lo recuerdo una vez.
  Con el ramo y la fecha en el nombre (`Microeconomía 05.10.26.m4a`) no pregunto nada.
- **El bot:** escríbele en Telegram. El menú tiene `/hoy`, `/evaluaciones`, `/pendientes`, `/resumenhtmlparasolemne` y `/nuevaconversacion`.
- **Si faltaste a una clase o cambió una fecha:** cuéntaselo al bot. Te pregunta con un botón antes de anotarlo.

## Cómo funciona por dentro

1. **Transcribe** con Whisper, en tu Mac. Si la clase es larga, la parte en tramos de 20 minutos.
2. Un agente **destila** la transcripción en notas de Obsidian con la skill
   [`transcripciones-a-conocimiento`](.claude/skills/transcripciones-a-conocimiento).
3. Un **segundo agente**, en una sesión aparte que no escribió nada, revisa esas notas contra
   la transcripción y manda a corregir lo que no tenga respaldo.
4. Condensa las notas en la **hoja de repaso**, archiva el audio, anota los anuncios del
   profesor y actualiza la **hoja de la evaluación**.
5. Avisa en el Mac y repite en Telegram solo los avisos finales.

Tres procesos de macOS lo mantienen andando solo: el vigilante de AirDrop, el bot y la
secretaria. El mapa completo, los comandos y los archivos de estado están en
[docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Decisiones técnicas

**El revisor es una sesión aparte, a propósito.** Nadie mira el material antes de que entre
a tu bóveda, así que la revisión no puede ser una autocrítica de quien lo escribió.

**Solo va lo que el audio respalda.** Un anuncio de evaluación o un pedido del profesor se
anota solo si su cita está en la transcripción. El bot, si algo no tiene respaldo, lo dice y
propone cómo averiguarlo.

**Privado por diseño.** De OneDrive el agente solo lee lo que pasa una lista blanca: las
pautas de evaluaciones pasadas, los enunciados y el programa que declaraste a mano. Nunca
lee tu correo, tu calendario ni el resto de la carpeta del semestre. Los conectores de
claude.ai van siempre apagados.

**Costo cero.** Whisper corre en el Mac y el modelo es tu plan Claude Pro. Cada llamada
queda medida en `orquestador/logs/uso.jsonl`.

## La skill que hace el trabajo

Quien decide qué se escribe es una skill de Claude Code en
[`.claude/skills/transcripciones-a-conocimiento/`](.claude/skills/transcripciones-a-conocimiento):

| Archivo | Qué contiene |
|---|---|
| [`SKILL.md`](.claude/skills/transcripciones-a-conocimiento/SKILL.md) | El flujo en fases y las reglas de honestidad que mandan sobre todo lo demás |
| [`references/limpieza-y-reconstruccion.md`](.claude/skills/transcripciones-a-conocimiento/references/limpieza-y-reconstruccion.md) | Cómo convertir habla transcrita en una fuente confiable |
| [`references/metodo-mit.md`](.claude/skills/transcripciones-a-conocimiento/references/metodo-mit.md) | Los seis pasos del método de estudio activo |
| [`references/diseno-documento.md`](.claude/skills/transcripciones-a-conocimiento/references/diseno-documento.md) | Cómo se arma la nota de aprendizaje |
| [`references/formato-obsidian.md`](.claude/skills/transcripciones-a-conocimiento/references/formato-obsidian.md) | Las plantillas de las notas de la bóveda |

Esta copia está acoplada al pipeline: recibe el ramo y la carpeta ya resueltos y termina con
una línea `RESULTADO_ORQUESTADOR` que lee `skill_runner.py`. Invocada a mano no funciona tal
cual.

## Instalación

La guía completa del pipeline de clases está en [docs/INSTALACION.md](docs/INSTALACION.md),
escrita para el orquestador original. Lo que este agente agrega se instala con:

```bash
bash launchd/instalar_vigilante.sh
bash launchd/instalar_bot.sh
```

El bot necesita su token en el Llavero de macOS (servicio `agente-estudio-telegram`) y el
chat emparejado: `python3 -m orquestador.bot --emparejar`.

## Privacidad

Tus grabaciones, tus notas y tus rutas reales nunca entran al repositorio. La lista exacta
de lo que queda fuera, con el porqué de cada cosa, está en `.gitignore`.
