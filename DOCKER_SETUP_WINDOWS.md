# Sapio AI Backend — Docker Setup (Windows 10/11)

This is a **local-development/team-sharing** Compose configuration, not a production deployment. It provides FastAPI, a separate always-running job worker, PostgreSQL 16, FFmpeg and Python/Piper packages. The host PC requires **Docker Desktop (Linux containers / WSL2)**, Docker Compose v2 and sufficient disk/RAM. Python, pip, FFmpeg and PostgreSQL are **not** required on teammates' PCs.

## 0. Compatibility note

This kit was based on the previously uploaded backend snapshot plus the previously shared popular-media patch. The **current backend should contain** `app/api/routes/popular_media.py` and migration `c43a1d56f902_popular_media_details.py`. Do not replace your existing backend code with the older snapshot. Check that all later changes are committed. If migrations are missing, resolve that before startup. Docker cannot correct missing source code or broken migrations.

## 1. Install Docker Desktop

Windows PowerShell (admin if installation requires it):

```powershell
winget install -e --id Docker.DockerDesktop
```

Restart Windows if requested, start Docker Desktop, select Linux containers/WSL2, wait for "Engine running". Verify:

```powershell
docker version
docker compose version
```

## 2. Place files in the backend root

Copy `Dockerfile`, `compose.yaml`, `.dockerignore` and `.env.docker.example` into the same folder as `requirements.txt`, `app/`, `migrations/`, `scripts/` and `alembic.ini`.

```
SapioAI/
  sapio_backend/
    Dockerfile
    compose.yaml
    .dockerignore
    .env.docker.example
    .env.docker                  # LOCAL ONLY, never commit
    requirements.txt
    alembic.ini
    app/
    migrations/
    scripts/
    models/
      piper/
        en_US-lessac-medium.onnx
        en_US-lessac-medium.onnx.json
        bn_BD-google-medium.onnx
        bn_BD-google-medium.onnx.json
```

**Model files:** Download the two Piper voice models **and their matching JSON configuration files** from the official Piper Voices repository, preserving the exact filenames above. Consult the specific voices used in `app/services/tts_service.py` if the code changes. These files are not bundled with the Docker image and are not fetched by Compose. Obtain them before testing generation. `models/piper` is bind-mounted read-only; the files remain on the teammate's PC. Official voice index: https://huggingface.co/rhasspy/piper-voices/tree/main

## 3. Set environment variables

```powershell
cd E:\SapioAI\sapio_backend
Copy-Item .env.docker.example .env.docker
notepad .env.docker
```

Replace the placeholder values for `POSTGRES_PASSWORD`, `DATABASE_URL` (same password, correctly URL-encoded), `JWT_SECRET_KEY` (>=32 UTF-8 bytes) and `GEMINI_API_KEY`. Never commit `.env.docker` or your private `.env`.

Generate an alphanumeric password and a secret using PowerShell if desired:

```powershell
$alphabet='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
-join (1..40 | ForEach-Object { $alphabet[ [System.Security.Cryptography.RandomNumberGenerator]::GetInt32($alphabet.Length) ] })
```

Use separate generated strings for PostgreSQL and JWT. Set `CORS_ALLOWED_ORIGINS` to the exact local frontend origins (JSON array).

## 4. Build the image and start PostgreSQL

```powershell
docker compose --env-file .env.docker build
docker compose --env-file .env.docker up -d db
docker compose --env-file .env.docker ps
```

The first build downloads the official Python and PostgreSQL images and pip packages. **Internet is needed on the image-building PC**. Teammates using exported images can run offline once they have both images and the model files.

## 5. Apply migrations and seed options — do this BEFORE starting the API/worker

```powershell
docker compose --env-file .env.docker run --rm --no-deps api python -m alembic upgrade head
```

The `--no-deps` flag works because DB was started in Step 4. Then:

```powershell
docker compose --env-file .env.docker run --rm --no-deps api python -m scripts.seed_roles
docker compose --env-file .env.docker run --rm --no-deps api python -m scripts.seed_project_options
```

Create an admin account interactively (the password prompt is not echoed):

```powershell
docker compose --env-file .env.docker run --rm --no-deps -it api python -m scripts.create_admin
```

If `-it` is rejected by your Compose version, use `docker compose --env-file .env.docker run --rm api python -m scripts.create_admin` in an interactive terminal.

## 6. Start the API and worker

```powershell
docker compose --env-file .env.docker up -d api worker
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f api worker
```

Open http://127.0.0.1:8000/docs and call the health endpoint; the exact health path can be confirmed in Swagger. The worker must remain running for summary and audio jobs.

**If the current frontend uses the Vite proxy** to `http://127.0.0.1:8000`, start frontend as usual (`npm install`, `npm run dev`). For the Dockerized backend only, the frontend runs on your Windows host. Docker Compose does **not** include the frontend.

## 7. Smoke tests

```powershell
docker compose --env-file .env.docker exec api python -c "from app.core.config import get_settings; print(get_settings().PIPER_MODELS_DIR)"
docker compose --env-file .env.docker exec api ffmpeg -version
docker compose --env-file .env.docker exec api python -m alembic current
docker compose --env-file .env.docker exec worker python -c "from piper import PiperVoice; print('Piper import OK')"
```

Then in Swagger/frontend: register/login, upload a **small** PDF, extract, create project, enqueue summary, wait for completion, enqueue audio, play generated audio and verify history. Also test admin popular-summary publishing and audio stream. Check logs on failures:

```powershell
docker compose --env-file .env.docker logs --tail=100 api
docker compose --env-file .env.docker logs --tail=100 worker
```

The previously reported `150 passed` applies to the earlier local regression tests; **Dockerized end-to-end tests still need to be run**.

## 8. Sharing IMAGE FILES with a teammate

`docker save` exports **images, NOT named volumes, PostgreSQL data, uploaded PDFs, generated audio or voice model files**. The teammate needs the setup files, an `.env.docker`, and the four voice model files in `models/piper`.

On the build PC:

```powershell
docker image save -o sapio-backend-image.tar sapio-backend:local
docker image save -o sapio-postgres-image.tar postgres:16-bookworm
```

Share both tar files privately (not via GitHub source). On the teammate's PC:

```powershell
docker load -i .\sapio-backend-image.tar
docker load -i .\sapio-postgres-image.tar
```

Place `compose.yaml`, `.env.docker`, the voice files and backend source tree in the same structure; then follow Steps 3–7 **without running `docker compose build`**. Compose uses `sapio-backend:local` loaded locally. For truly source-free use, make a slim distribution folder containing `compose.yaml`, `.env.docker` and `models/piper/`, and remove the `build:` section from the Compose file so it never tries to build.

A Docker image created on x86_64 Windows Docker Desktop typically needs an amd64-compatible recipient; use matching target architectures. Linux containers are not ordinary Windows executables; Docker Desktop is still required.

## 9. Persistent volumes and path consistency

The containers always use `/app/storage/...`. Docker named volumes preserve DB and generated media across container restart/recreate. `docker compose down` does not delete them; **`docker compose down -v` DOES DELETE YOUR DATABASE AND MEDIA**. Do not run `down -v` without backups.

Popular-summary media paths are absolute in your current backend. New media created inside Docker are saved as `/app/storage/popular_summaries/...`, which remains stable across teammate PCs. Existing Windows database rows containing `E:\...` paths **will not resolve inside Linux containers**: migrate DB/media paths carefully if reusing old data. A fresh teammate DB has no such Windows paths.

## 10. Troubleshooting

- **Docker unavailable**: start Docker Desktop and enable WSL2/Linux containers.
- **Port 8000 occupied**: stop your local `uvicorn`, or change `127.0.0.1:8000:8000` to `127.0.0.1:8001:8000` in Compose and Vite proxy accordingly.
- **Postgres auth failure**: check that `POSTGRES_PASSWORD` and password in `DATABASE_URL` match. Environment changes do not reset an existing initialized PostgreSQL volume.
- **`No module app...`**: verify copied source has `app/`, `migrations/` and `scripts/`, and rebuild the image after changes.
- **Piper model not found**: put `*.onnx` AND `*.onnx.json` into `models/piper`; verify filenames and `docker compose ... exec worker ls /app/models/piper`.
- **Audio stream 404 after moving from Windows**: check old DB absolute media paths; do not blindly rewrite paths without verifying real files.
- **Worker crashes**: check `docker compose ... logs worker`. API availability alone does not prove generation works.
- **`alembic upgrade head` fails**: ensure the final migration files exist and form a single valid chain; do not create new migrations blindly.
- **Permissions errors**: named Docker volumes use Linux permissions. The image runs as non-root UID 10001.

## 11. Security

This kit binds port 8000 to `127.0.0.1`, not the public network. It is for **local testing**, not internet exposure. Use a real reverse proxy/TLS, production secrets, backups, a controlled migration process, monitoring, restricted admin access, and appropriate storage for production. Use `docker compose ... config` cautiously: it may print secrets, so do not share its raw output.

### Git-ignore these

```
.env
.env.docker
.env.*
!.env.example
!.env.docker.example
models/piper/*.onnx
models/piper/*.onnx.json
storage/
*.tar
```

Never commit real API keys (including to `.env.example`). Rotate any exposed key.
