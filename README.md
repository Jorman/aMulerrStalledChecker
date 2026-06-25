# aMulerr Stalled Checker
![Docker Pulls](https://img.shields.io/docker/pulls/chryses/amulerr-stalled-checker)
![Docker Image Size](https://img.shields.io/docker/image-size/chryses/amulerr-stalled-checker)
![GitHub](https://img.shields.io/github/license/Jorman/aMulerrStalledChecker)

> Automated monitoring and cleanup tool for stalled downloads in Sonarr/Radarr via aMulerr

---

## Overview

aMulerr Stalled Checker is a Docker-based monitoring service that automatically detects and removes stalled or source-less downloads from [aMulerr](https://github.com/isc30/aMulerr), keeping your [Sonarr](https://github.com/Sonarr/Sonarr) and [Radarr](https://github.com/Radarr/Radarr) download queues clean and efficient.

When aMulerr downloads get stuck without sources or stall indefinitely, this tool identifies them through configurable health checks, removes them from aMulerr, marks them as failed in the respective *Arr application, and automatically triggers a new search. This ensures your media automation keeps running smoothly without manual intervention.

---

## ✨ Features

- 🧠 **Smart Stall Detection** — Configurable checks before marking downloads as stalled
- 🧹 **Automatic Cleanup** — Removes stalled downloads and triggers new searches
- 🗂️ **Category-Based Management** — Handles Sonarr and Radarr downloads separately via categories
- 🧭 **Orphan Detection** — Removes downloads that exist only in aMulerr (optional)
- 👀 **Monitoring-Aware** — Respects series/season/episode/movie monitoring status
- ⏰ **Grace Period** — Configurable waiting time for recent downloads
- 🔔 **Apprise Notifications** — Multi-service alerts (Telegram, Discord, Email, Slack, Pushover, etc.)
- 🐳 **Docker Native** — Easy deployment and management
- 🧪 **Dry Run Mode** — Test configuration without actual changes
- 📜 **Detailed Logging** — Console and optional file logging with configurable levels

---

## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- Running instances of:
  - [aMulerr](https://github.com/isc30/aMulerr)
  - [Sonarr](https://github.com/Sonarr/Sonarr) and/or [Radarr](https://github.com/Radarr/Radarr)
- aMulerr configured as a download client (type: `qBittorrent`) in Sonarr/Radarr with specific categories.

### Using Docker Compose

```yaml
version: '3.8'

services:
  amulerr-stalled-checker:
    image: chryses/amulerr-stalled-checker:latest
    container_name: amulerr-stalled-checker
    restart: unless-stopped
    environment:
      - TZ=Europe/Rome
      - CHECK_INTERVAL=10
      - AMULERR_HOST=http://your-amulerr-ip:3000
      - STALL_CHECKS=30
      - STALL_DAYS=15
      - RECENT_DOWNLOAD_GRACE_PERIOD=30
      - DELETE_IF_UNMONITORED_SERIE=false
      - DELETE_IF_UNMONITORED_SEASON=false
      - DELETE_IF_UNMONITORED_EPISODE=true
      - DELETE_IF_UNMONITORED_MOVIE=true
      - DELETE_IF_ONLY_ON_AMULERR=false
      - DOWNLOAD_CLIENT=amulerr
      - RADARR_HOST=http://your-radarr-ip:7878
      - RADARR_API_KEY=your_radarr_api_key
      - RADARR_CATEGORY=radarr-aMulerr
      - SONARR_HOST=http://your-sonarr-ip:8989
      - SONARR_API_KEY=your_sonarr_api_key
      - SONARR_CATEGORY=tv-sonarr-aMulerr
      - APPRISE_URLS=pover://user_key@app_token
      - LOG_LEVEL=info
      - LOG_TO_FILE=/logs
      - DRY_RUN=false
    volumes:
      - ./logs:/logs
```

---

## ⚙️ Configuration

### Environment Variables

#### Core Settings

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AMULERR_HOST` | aMulerr base URL (e.g., `http://10.0.0.100:3000`). Must start with `http://` or `https://` | — | ✅ Yes |
| `CHECK_INTERVAL` | Minutes between stall checks | — | ✅ Yes |
| `STALL_CHECKS` | Number of consecutive checks before marking as stalled | — | ✅ Yes |
| `STALL_DAYS` | Days before a never-completed download is considered stalled | — | ✅ Yes |
| `RECENT_DOWNLOAD_GRACE_PERIOD` | Minutes to wait before checking recent downloads | `30` | ✅ Yes |

#### *Arr Integration (at least one required)

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DOWNLOAD_CLIENT` | Download client name configured in Sonarr/Radarr | — | ✅ Yes |
| `RADARR_HOST` | Radarr base URL | `None` | ⚠️ Conditional |
| `RADARR_API_KEY` | Radarr API key | `None` | ⚠️ Conditional |
| `RADARR_CATEGORY` | aMulerr category for Radarr downloads | `None` | ⚠️ Conditional |
| `SONARR_HOST` | Sonarr base URL | `None` | ⚠️ Conditional |
| `SONARR_API_KEY` | Sonarr API key | `None` | ⚠️ Conditional |
| `SONARR_CATEGORY` | aMulerr category for Sonarr downloads | `None` | ⚠️ Conditional |

#### Monitoring & Cleanup Rules

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DELETE_IF_UNMONITORED_SERIE` | Remove downloads for unmonitored series (Sonarr) | `false` | ❌ No |
| `DELETE_IF_UNMONITORED_SEASON` | Remove downloads for unmonitored seasons (Sonarr) | `false` | ❌ No |
| `DELETE_IF_UNMONITORED_EPISODE` | Remove downloads for unmonitored episodes (Sonarr) | `false` | ❌ No |
| `DELETE_IF_UNMONITORED_MOVIE` | Remove downloads for unmonitored movies (Radarr) | `false` | ❌ No |
| `DELETE_IF_ONLY_ON_AMULERR` | Remove orphaned downloads (present only in aMulerr, not in *Arr) | `false` | ❌ No |

#### 🔔 Notifications

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `APPRISE_URLS` | One or more Apprise URLs (space or comma separated), e.g., `pover://user@app` | `None` | ❌ No |

#### 🪵 Logging & Debug

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `LOG_LEVEL` | Logging level: `debug`, `info`, `warning`, `error`, `critical` | `info` | ❌ No |
| `LOG_TO_FILE` | Directory path where the log file will be created as `amulerr_stalled_checker.log` | `None` | ❌ No |
| `DRY_RUN` | Test mode — no actual deletions (`true`/`false`) | `false` | ❌ No |
| `TZ` | Timezone (e.g., `Europe/Rome`) | `UTC` | ❌ No |

