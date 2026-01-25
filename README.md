# Hylla 📀

![Build Status](https://github.com/Mezhona/Hylla/actions/workflows/publish.yml/badge.svg)
![License](https://img.shields.io/github/license/Mezhona/Hylla)
![Docker Image Size](https://img.shields.io/docker/image-size/ghcr.io/mezhona/hylla/latest)

**Hylla** (Norwegian for *"Shelf"*) is a robust, self-hosted media manager designed for collectors who straddle the line between physical media and digital libraries. 

While Plex/Jellyfin manage your *files*, **Hylla** manages your *ownership*.

## ✨ Features

* **Physical vs. Digital Tracking:** Distinctly track which movies you own on Blu-ray/DVD versus what you have digitally available.
* **Auto-Metadata:** Automatically fetches posters, plot summaries, and release dates via TMDB/OMDB APIs.
* **Audit Logging:** A persistent, immutable history of every change (e.g., *"User X changed rating from 4.0 → 5.0"*).
* **Enterprise Security:** Built-in support for OIDC (OpenID Connect) to integrate with Authentik, Keycloak, or Google.
* **Self-Healing Database:** The container automatically initializes its own database tables on the first run.

---

## 🚀 Quick Start (No Building Required)

You do not need to download the source code to run Hylla. You can simply use `docker-compose` to pull the latest pre-built image.

### 1. Create a folder
Create a folder on your server (e.g., `hylla`) and enter it.

### 2. Create the `docker-compose.yml`
Create a file named `docker-compose.yml` and paste the following configuration:

```yaml
services:
  # Database Service
  db:
    image: mariadb:10.11
    container_name: hylla_db
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: change_me_root    # <--- CHANGE THIS
      MYSQL_DATABASE: hylla_db
      MYSQL_USER: hylla_user
      MYSQL_PASSWORD: change_me_db_pass    # <--- CHANGE THIS (Match below)
    volumes:
      - db_data:/var/lib/mysql
    networks:
      - hylla_net
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Application Service
  hylla:
    image: ghcr.io/mezhona/hylla:latest
    container_name: hylla_app
    restart: unless-stopped
    ports:
      - "5051:5000"
    depends_on:
      db:
        condition: service_healthy
    environment:
      # Database Connection
      - DB_HOST=db
      - DB_PORT=3306
      - DB_NAME=hylla_db
      - DB_USER=hylla_user
      - DB_PASSWORD=change_me_db_pass    # <--- Must match MYSQL_PASSWORD above
      
      # Security
      - FLASK_SECRET_KEY=change_me_to_a_long_random_string
      
      # Optional Integrations (Leave blank if not used)
      - TMDB_API_KEY=
      - OMDB_API_KEY=
      - OIDC_CLIENT_ID=
      - OIDC_CLIENT_SECRET=
      - OIDC_METADATA_URL=
    volumes:
      - ./uploads:/app/static/uploads
    networks:
      - hylla_net

volumes:
  db_data:

networks:
  hylla_net:
