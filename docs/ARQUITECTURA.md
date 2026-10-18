# Arquitectura y comandos

Cómo se trabaja sobre Agente estudio: los comandos de desarrollo y cómo encajan las piezas.
Vive aquí y no en el `CLAUDE.md` de la raíz porque ese archivo entra en cada llamada del
pipeline a la skill (`setting_sources=["project"]`), y esto no le sirve a la skill.

El README explica qué hace el agente y cómo se usa día a día. Este documento es para quien
trabaja sobre el código.

## Comandos

Desde la raíz del repositorio, siempre con el Python 3.13 del instalador oficial
(`/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`, abajo `$PY`):

- **Todas las pruebas:** `$PY tests/test_orquestador.py`. Es un runner propio, no pytest:
  ejecuta cada función `probar_*` en orden. No gastan tokens y nunca escriben al Telegram
  real.
- **Una sola prueba:**
  `$PY -c "import sys; sys.path.insert(0, 'tests'); import test_orquestador as t; t.probar_<nombre>(); print(t.fallos)"`
- **Nombres indefinidos o sin usar:** `$PY -m ruff check --select F orquestador tests`. El hook
  de commit lo corre sobre los `.py` del commit. Se instala con `$PY -m pip install ruff`.
- **Salud del sistema** (rutas, Whisper, sesión de Claude): `$PY -m orquestador.verificar`.
  Hace una llamada mínima al modelo por defecto y otra por cada modelo que el código pide por
  nombre (`bot.MODELO`, `reuniones.MODELO`).
- **Ensayo completo contra un sandbox**, sin tocar el vault ni mover audios:
  `$PY -m orquestador.ensayo [<slug>] [--conservar]`. Este sí gasta tokens.
- **Procesar Input a mano**, lo mismo que hace el vigilante: `$PY -m orquestador.procesar_input`.
- **Rehacer una hoja:** `$PY -m orquestador.hoja_html <slug> --pisar` para una clase, y
  `$PY -m orquestador.hoja_evaluacion "<RAMO>" <S1|S2|Examen>` para una evaluación.
- **Procesar una reunión a mano:** `$PY -m orquestador.reuniones <transcripcion.txt> <AAAA-MM-DD> "<nombre>"`.
- **Reproducir un fallo que solo pasa en segundo plano** (OneDrive, Llavero, permisos):
  `bash launchd/probar_en_launchd.sh <script.py>` corre el script como lo corren los
  LaunchAgents y muestra lo que imprimió.

El bot es un proceso que no se recarga solo. Si cambias código que él usa, reinícialo:

```bash
launchctl kickstart -k gui/$(id -u)/local.agente-estudio.bot
```

`airdrop` y `secretaria` arrancan de cero en cada corrida, así que ya leen el código nuevo sin reiniciar nada.

Los registros quedan en `orquestador/logs/`: `bot.log`, `errores.log`, `uso.jsonl`, con
el consumo de cada llamada, y `opiniones.jsonl`, con cada 👍/👎 sobre una respuesta del bot
(pregunta, respuesta, sesión, voto y el motivo si lo dio). Cada línea es completa: para
analizar, vale la última de cada `mensaje`. El campo `sesion` de `uso.jsonl` lleva a la conversación
completa, en `~/.claude/projects/<carpeta del proyecto>/<sesion>.jsonl`. La última
actividad queda en `Estado.txt`.

## Cómo encaja todo

Cuatro LaunchAgents corren solos. Los instalan `launchd/instalar_vigilante.sh`,
`launchd/instalar_bot.sh` y `launchd/instalar_grabadora.sh`, que arman los `.plist`
con las rutas de este Mac.

| LaunchAgent | Módulo | Cuándo corre |
|---|---|---|
| `local.agente-estudio.airdrop` | `vigilar_descargas` | Cuando cambia Descargas (AirDrop), `Input/` o la carpeta `rutas.grabaciones_onedrive`, y cada 10 minutos. Si cambias esas rutas, vuelve a correr `launchd/instalar_vigilante.sh` |
| `local.agente-estudio.bot` | `bot` | Siempre, esperando mensajes de Telegram |
| `local.agente-estudio.secretaria` | `secretaria` | Todos los días a las 9:00. Si no hay nada que decir, no escribe |
| `local.agente-estudio.grabadora` | `grabadora/Grabadora.app` | Siempre, como botón en la barra de menú para grabar reuniones on-line |

La grabadora (`grabadora/Grabadora.swift`) graba el audio del sistema con un tap de Core
Audio, el micrófono con AVAudioEngine (un tramo nuevo cada vez que macOS cambia de
micrófono) y, si se pide, la pantalla con ScreenCaptureKit. Al detener, deja tu voz en el
canal izquierdo y la llamada en el derecho, con ffmpeg, y deja `Reunión Online DD.MM.YY - <tema>.m4a` en Input, donde lo toma el vigilante.
`ramo_por_nombre` reconoce el prefijo como REUNIÓN, salvo que el tema sea un ramo:
ahí entra como clase de ese ramo. Las pistas sueltas quedan en
`archivos para borrar/Agente estudio - pistas de la grabadora/`. Se compila con
`boton_app/compilar_apps.sh` y se instala con `bash launchd/instalar_grabadora.sh`.
`transcripcion` transcribe cada canal aparte y marca cada frase como "Tú" u "Otros".
La pausa (solo en modo audio) deja de escribir sin cerrar nada, y los tramos se ubican
en la mezcla con un cronómetro que descuenta las pausas. Cada 15 minutos de pausa avisa
en el Mac y por Telegram. Su prueba es `Grabadora --probar`, que corre al compilar.

El camino de una clase:

1. `vigilar_descargas` reconoce lo que llegó por AirDrop (cuarentena puesta por
   `sharingd`), lo mueve a `Input/` y llama a `procesar_input`, que toma el candado de
   `lock.py`.
2. `deteccion` agrupa las grabaciones y resuelve el ramo: primero por el nombre del archivo,
   y si no, por el día de la semana de `config.json`. `pregunta_ramo` decide si hace falta
   preguntar: si el nombre dice el ramo y la fecha, no pregunta. Si no, pregunta por Telegram
   con un botón por ramo, la grabación queda en `Input/` y el bot, al recibir el botón, anota
   la respuesta y relanza `procesar_input --esperar`. La secretaria recuerda una vez lo que
   sigue sin respuesta. La ventana del Mac (`pantalla_confirmacion`) ya no se llama.
3. `transcripcion` importa `backend.transcriber` de Transcriptotem, que vive en OneDrive (ruta
   en `config.json`), y deja el texto en `orquestador/transcripciones_pendientes/`. Ese
   intermedio permite reintentar sin volver a transcribir. Al lado deja
   `<slug>_whisper.json`, la salida cruda de Whisper: cada segmento con sus tiempos y su
   confianza, y cada palabra con su probabilidad. Es para medir la transcripción sin volver
   a pasar el audio por Whisper. Ninguna etapa lo lee todavía.
4. `finalizar_clase` encadena el resto: `skill_runner` (la skill escribe las notas en el
   vault de Obsidian), `revisor` en sesión aparte, la corrección, los anuncios y pedidos del
   profesor hacia `evaluaciones.json`, `hoja_html`, el `archivado` del audio y su
   publicación en `<carpeta del ramo>/Clases/` de OneDrive (audio comprimido movido,
   transcripción y hoja copiadas, con el mismo nombre), y `hoja_evaluacion`. La carpeta de
   cada ramo la declara `carpetas_onedrive` en `config.json`. `Procesados/` y `Output/`
   quedan como sala de trabajo. Una clase con `<slug>_skill.json` ya está escrita.
   Antes de la skill, `material_ramo` deja en `<slug>_material.txt` el programa declarado
   en `programas` y, si calza con la clase, una lámina de `PPTs/`. Lo leen la skill y el
   revisor. El revisor además pone el puntaje de 1 a 100 que la hoja muestra arriba.
   La hoja de evaluación suma las láminas de `PPTs/` y las ayudantías de `Ayudantias/` de las
   clases que entran: cada PDF se asigna a su clase por la fecha del nombre o por parecido.
   También el código de `Código R/`. La hoja de un control se pide a mano:
   `python3 -m orquestador.hoja_evaluacion "<RAMO>" Control <AAAA-MM-DD>`.
5. `notificaciones` avisa en el Mac y repite en Telegram solo los avisos finales.

Una grabación con ramo `REUNIÓN` se desvía a `reuniones.py`. Deja una nota en `Reuniones/`
del vault y un `.docx` en OneDrive, con el audio comprimido y la transcripción al lado y con
el mismo nombre, manda las acciones a los pendientes y las promesas con
plazo de la otra parte como seguimientos (solo lo que tiene cita en la transcripción, que
Python comprueba) y ofrece un botón
que abre Google Calendar con el evento lleno.

Datos de estado (cuáles quedan fuera de git lo dice `.gitignore`):

- `orquestador/config.json`: rutas, el ramo, horario y sala de cada día, y el chat de
  Telegram. Su plantilla es `config.example.json`.
- `orquestador/evaluaciones.json`: S1, S2 y Examen de cada ramo, los `pendientes` (controles,
  tareas y pedidos con fecha, con `estado: hecho` los que el estudiante ya cumplió), las
  `clases_perdidas`, las `clases_suspendidas` (fecha sin clase, no cuenta como perdida) y los
  `feriados`. Cuando dos fuentes
  chocan, manda lo que dijo el profesor en clase, después lo que dijo el estudiante por el
  chat y al final el calendario. El pipeline solo anota un anuncio si su cita está en la
  transcripción.
- `orquestador/bot_estado.json`: la sesión del bot, que dura 3 horas, y la acción que espera
  confirmación con un botón. Para leer qué respondió el bot, toma su `session_id` y abre
  `~/.claude/projects/<carpeta del proyecto con guiones>/<session_id>.jsonl`. Las sesiones
  anteriores están en `logs/uso.jsonl`, etapa `bot`, campo `sesion`. Guarda también las
  últimas 20 respuestas, para que un 👍/👎 sepa a qué pregunta corresponde.
- `orquestador/preguntas_grabaciones.json`: las grabaciones que esperan que el estudiante
  diga de qué son (`pregunta_ramo`), con su respuesta cuando llega.
- `orquestador/propuestas_memoria/`: lo que una reunión ofreció guardar en la wiki, un
  archivo por oferta, marcado como respondido cuando se toca el botón.

**Solo el bot escucha a Telegram.** Cuando otro proceso (el procesamiento, la secretaria, el
cierre de una reunión) necesita una respuesta con botones, envía el mensaje y deja lo que
espera en un archivo de estado. El bot recibe el botón y escribe la respuesta en ese mismo
archivo. Si dos procesos pueden escribirlo, va con candado (`evaluaciones._modificando`, y
el mismo patrón en `pregunta_ramo`). Si no, cada uno escribe en un momento distinto
(`propuestas_memoria/`). Un botón nuevo sigue este patrón: no agregues otro proceso que
lea `getUpdates`.

Piezas del agente que viven fuera de este repo, con su propio respaldo:

- `backend/transcriber.py` de Transcriptotem, en OneDrive (ruta en `config.json`). Sin git:
  git dentro de OneDrive se corrompe al sincronizar. Antes de cambiarlo, copia el archivo a
  `_herramientas/respaldos/` del contenedor.
- En `_herramientas/` del contenedor, que tiene git local sin remote: el hook compartido de
  privacidad (`git-hooks/`), la auditoría de publicación
  y la carga de la lista privada del Llavero.

Piezas que cruzan todo el sistema:

- **Toda llamada al modelo pasa por `skill_runner.consultar`, nunca por `query` directo.**
  Apaga los conectores de claude.ai, reintenta una vez si Claude
  rechaza la credencial y conserva el motivo real de un error del SDK. La conexión es la
  sesión normal de Claude Code guardada en el Llavero, que se renueva sola. El token de
  `claude setup-token` no sirve aquí: el SDK no lo usa y, puesto, impedía renovar la
  sesión (ver el comentario en `consultar`).
- **Para probar la conexión con Claude, usa `launchd/probar_en_launchd.sh`.** Una sesión de
  la app de Claude hereda variables como `ANTHROPIC_BASE_URL`, que hacen pasar la
  autenticación por la propia app: desde ahí una llamada funciona aunque la credencial
  esté mal, y la prueba no dice nada.
- **`orquestador/__init__.py` deja que los procesos de launchd descarguen** los archivos que
  OneDrive dejó solo en la nube. Sin eso, la lectura falla con Errno 11.
- **Los secretos viven en el Llavero de macOS**, nunca en archivos ni en la salida de un
  comando: el token del bot (`agente-estudio-telegram`) y la sesión de Claude Code
  (`Claude Code-credentials`, la maneja el propio Claude Code).
- **El agente nunca lee el calendario, el correo ni la carpeta completa del semestre** en
  OneDrive, que tiene contratos y planillas con datos personales. De OneDrive solo entra lo
  que pasa una lista blanca por nombre (`onedrive.elegir_archivos` y los programas de ramo).
- **Lo que va a Telegram es texto plano**, sin markdown, con un emoji al inicio de cada
  bloque: el estudiante lo lee en el teléfono, caminando. El tono del bot está en
  `orquestador/plantilla_bot/instrucciones.md`.
