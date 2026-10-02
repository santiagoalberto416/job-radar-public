# Portal de job-radar

Portal web local para job-radar: ver y filtrar las ofertas guardadas, revisar el estado de los servicios y las
fuentes, ejecutar comandos y editar `config.yaml`, `profile.md` y `.env`. API en Node.js + Express e interfaz en
React + Vite + TypeScript. Funciona en escritorio y celular, y se puede instalar como app en el iPhone.

**Solo corre en tu Mac:** escucha en `127.0.0.1:4747` y rechaza peticiones de otros hosts o sitios web (valida
Host y Origin, y las escrituras necesitan un encabezado que las peticiones de otros sitios no pueden enviar). Los
secretos de `.env` nunca llegan al navegador: solo si están puestos y sus últimos 4 caracteres.

## Correrlo

Necesita Node ≥ 22.5 (usa `node:sqlite`, incluido en Node).

```bash
cd portal
npm install
npm start            # construye la interfaz y sirve todo en http://127.0.0.1:4747
```

Siempre encendido, como servicio de launchd (arranca al iniciar sesión y se reinicia si falla):

```bash
portal/scripts/install_portal.sh      # luego abre http://127.0.0.1:4747
portal/scripts/uninstall_portal.sh    # detenerlo
tail -f ~/Library/Logs/job-radar-portal.log
```

El servicio usa el `node` que encuentre al instalar (la ruta de nvm, si usas nvm). Si cambias de versión de Node,
vuelve a correr el script. Otro puerto: `PORTAL_PORT=5050 portal/scripts/install_portal.sh`.

## Páginas

| Página | Qué hace |
|---|---|
| **Ofertas** | Todas las ofertas de `data/jobs.db`. Vistas rápidas (⭐ Mejores, 🆕 Últimas, 📨 Enviadas, 🗂️ Postulaciones, Todas), búsqueda y filtros por estado, fuente y fecha. Al tocar una ves el detalle: por qué la calificó Claude, alertas y la descripción, y puedes marcar 👍/👎, la etapa de tu postulación y notas |
| **Estado** | Los servicios de launchd, la última búsqueda, totales, crédito estimado de Claude, estado de cada fuente y el log. Botones para buscar ahora y reiniciar el bot |
| **Comandos** | Los comandos de la línea de comandos (una lista fija, con la salida en vivo) y los del bot de Telegram |
| **Configuración** | Formulario para lo común (puntaje, horario, modelo, crédito, fuentes), `config.yaml` completo y `profile.md`. El formulario cambia solo los valores: los comentarios y el formato del archivo se conservan |
| **Entorno (.env)** | Qué claves están puestas (enmascaradas); reemplazarlas o agregar variables. El archivo queda con permisos 600. Reinicia el bot después de cambiar una clave que use |

El portal solo escribe en la base de datos tu opinión (👍/👎), la etapa de tus postulaciones y tus notas. Las búsquedas leen `config.yaml`, `profile.md` y `.env` en
cada corrida, así que los cambios aplican desde la siguiente.

## Acceso desde el celular (ngrok, con login de Google)

Puedes abrir el portal desde cualquier lugar a través de un túnel de ngrok. Protecciones:

- **Login en ngrok:** nadie pasa sin iniciar sesión con Google con uno de los correos que autorices. Los demás
  reciben 403 en los servidores de ngrok, antes de llegar a tu Mac.
- **Sin editar archivos:** por el túnel puedes ver todo, ejecutar cualquier comando (incluido *Buscar ahora*),
  reiniciar el bot y marcar 👍/👎, postulaciones y notas, pero no cambiar `.env`, la configuración ni el perfil. La
  interfaz muestra "remoto".
- **Siempre con login:** el script no abre el túnel si la política no tiene las reglas de login, correo y bloqueo.

```bash
# 1. Crea una cuenta en ngrok.com, instala ngrok y conéctalo:  ngrok config add-authtoken <token>
# 2. Reclama tu dominio estático gratis en https://dashboard.ngrok.com/domains
# 3. Abre el túnel como servicio, con el/los correo(s) de Google autorizados:
portal/scripts/install_tunnel.sh tu-dominio.ngrok-free.dev tu-correo@gmail.com
# Cerrarlo (el portal vuelve a ser solo local):
portal/scripts/uninstall_tunnel.sh
tail -f ~/Library/Logs/job-radar-tunnel.log
```

La Mac tiene que estar despierta. En el plan gratuito, ngrok muestra una página de aviso la primera vez en cada
navegador y permite 20 mil peticiones al mes; las páginas remotas se actualizan menos seguido para no gastarlas.

### Instalarlo como app en el iPhone

En **Safari**, abre la URL del túnel, toca **Compartir → Agregar a pantalla de inicio**. Abre a pantalla completa
con su propio ícono. La primera vez tendrás que iniciar sesión con Google dentro de la app (las apps de pantalla de
inicio no comparten la sesión de Safari).

## Desarrollo

```bash
npm run dev        # API en 4747 (se recarga sola) + Vite en http://127.0.0.1:4748 con recarga en caliente
npm test           # tests del API (node:test) contra una copia temporal de los archivos del repo
npm run typecheck
```

Mapa del código: `server/` (API Express: rutas en `app.ts`, `security.ts`, `env.ts`, `config.ts`, `db.ts`,
`commands.ts`, `system.ts`), `src/` (páginas de React), `shared/types.ts` (tipos compartidos).
