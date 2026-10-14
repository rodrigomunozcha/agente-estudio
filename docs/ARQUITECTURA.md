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
- **Salud del sistema** (rutas, Whisper, sesión de Claude): `$PY -m orquestador.verificar`.
  Hace una llamada mínima al modelo.
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

Los registros quedan en `orquestador/logs/`: `bot.log`, `errores.log` y `uso.jsonl`, con
el consumo de cada llamada. El campo `sesion` de `uso.jsonl` lleva a la conversación
completa, en `~/.claude/projects/<carpeta del proyecto>/<sesion>.jsonl`. La última
actividad queda en `Estado.txt`.

## Cómo encaja todo

Tres LaunchAgents corren solos. Los instalan `launchd/instalar_vigilante.sh` y
`launchd/instalar_bot.sh`, que arman los `.plist` con las rutas de este Mac.

| LaunchAgent | Módulo | Cuándo corre |
|---|---|---|
| `local.agente-estudio.airdrop` | `vigilar_descargas` | Cuando llega un audio por AirDrop a Descargas, y cada 10 minutos |
| `local.agente-estudio.bot` | `bot` | Siempre, esperando mensajes de Telegram |
| `local.agente-estudio.secretaria` | `secretaria` | Todos los días a las 9:00. Si no hay nada que decir, no escribe |

El camino de una clase:

1. `vigilar_descargas` reconoce lo que llegó por AirDrop (cuarentena puesta por
   `sharingd`), lo mueve a `Input/` y llama a `procesar_input`, que toma el candado de
   `lock.py`.
2. `deteccion` agrupa las grabaciones y resuelve el ramo: primero por el nombre del archivo,
   y si no, por el día de la semana de `config.json`. `pantalla_confirmacion` muestra una
   ventana que sigue sola a los 10 minutos.
3. `transcripcion` importa `backend.transcriber` de Transcriptotem, que vive en OneDrive (ruta
   en `config.json`), y deja el texto en `orquestador/transcripciones_pendientes/`. Ese
   intermedio permite reintentar sin volver a transcribir.
4. `finalizar_clase` encadena el resto: `skill_runner` (la skill escribe las notas en el
   vault de Obsidian), `revisor` en sesión aparte, la corrección, los anuncios y pedidos del
   profesor hacia `evaluaciones.json`, `hoja_html`, el `archivado` del audio, la copia a
   OneDrive y `hoja_evaluacion`. Una clase con `<slug>_skill.json` ya está escrita.
5. `notificaciones` avisa en el Mac y repite en Telegram solo los avisos finales.

Una grabación con ramo `REUNIÓN` se desvía a `reuniones.py`. Deja una nota en `Reuniones/`
del vault y un `.docx` en OneDrive, manda las acciones a los pendientes y ofrece un botón
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
  anteriores están en `logs/uso.jsonl`, etapa `bot`, campo `sesion`.

Piezas del agente que viven fuera de este repo, con su propio respaldo:

- `backend/transcriber.py` de Transcriptotem, en OneDrive (ruta en `config.json`). Sin git:
  git dentro de OneDrive se corrompe al sincronizar. Antes de cambiarlo, copia el archivo a
  `_herramientas/respaldos/` del contenedor.
- En `_herramientas/` del contenedor, que tiene git local sin remote: el copiador de hojas a
  OneDrive, el hook compartido de privacidad (`git-hooks/`), la auditoría de publicación
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
