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
- 🔀 **Multi-Instance Support** — Connect multiple Radarr and/or Sonarr instances via pipe-separated env vars
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

Here is the basic standalone configuration for the checker:

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
      - GHOST_LINK_STALL_CHECKS=6  # Optional: checks before removing ghost links. Default: same as STALL_CHECKS
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

### Full Stack Integration (Recommended)

If you run `amule` and `amulerr` in the same Docker Compose stack, you can set up healthchecks and startup dependencies to ensure that the services start in the correct order:

```yaml
version: '3.8'

services:
  amule:
    container_name: amule
    image: ngosang/amule:develop
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/Rome
      - GUI_PWD=your_amule_gui_password
      - WEBUI_PWD=your_amule_webui_password
      - MOD_AUTO_RESTART_ENABLED=true
      - MOD_AUTO_RESTART_CRON=0 6 * * *
      - MOD_AUTO_SHARE_ENABLED=false
    network_mode: host
    volumes:
      - /path/to/amule/config:/home/amule/.aMule
      - /path/to/downloads/complete:/downloads/complete
      - /path/to/downloads/incomplete:/downloads/incomplete
    healthcheck:
      test: ["CMD-SHELL", "amulecmd --host=127.0.0.1 --port=4712 --password=$$GUI_PWD --command=status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
    restart: unless-stopped

  amulerr:
    container_name: amulerr
    image: isc30/amulerr:latest
    user: "1000:1000"
    environment:
      - AMULE_HOST=127.0.0.1
      - AMULE_PORT=4712
      - AMULE_PWD=your_amule_gui_password
    ports:
      - "3000:3000"
    depends_on:
      amule:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:3000/health || exit 1"]
      interval: 20s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped

  amulerr-stalled-checker:
    image: chryses/amulerr-stalled-checker:latest
    container_name: amulerr-stalled-checker
    environment:
      - TZ=Europe/Rome
      - CHECK_INTERVAL=10
      - AMULERR_HOST=http://127.0.0.1:3000
      - STALL_CHECKS=30
      - STALL_DAYS=15
      - RECENT_DOWNLOAD_GRACE_PERIOD=30
      - DELETE_IF_UNMONITORED_SERIE=false
      - DELETE_IF_UNMONITORED_SEASON=false
      - DELETE_IF_UNMONITORED_EPISODE=true
      - DELETE_IF_UNMONITORED_MOVIE=true
      - DELETE_IF_ONLY_ON_AMULERR=false
      - APPRISE_URLS=pover://your_user_key@your_app_token
      - LOG_LEVEL=info
      - LOG_TO_FILE=/logs
      - DRY_RUN=false
      - DOWNLOAD_CLIENT=amulerr
      - RADARR_HOST=http://your-radarr-ip:7878
      - RADARR_API_KEY=your_radarr_api_key
      - RADARR_CATEGORY=radarr-aMulerr
      - SONARR_HOST=http://your-sonarr-ip:8989
      - SONARR_API_KEY=your_sonarr_api_key
      - SONARR_CATEGORY=tv-sonarr-aMulerr
    volumes:
      - ./logs:/logs
    depends_on:
      amulerr:
        condition: service_healthy
    restart: unless-stopped
```


---

## 🔀 Multi-Instance Support

You can connect **multiple Radarr and/or Sonarr instances** by using pipe-separated (`|`) values for the host, API key, and category variables. Each position across the three variables maps to one instance — they must all have the **same number of entries** in the **same order**.

```yaml
# Two Radarr instances (e.g. HD + 4K)
- RADARR_HOST=http://radarr1:7878|http://radarr2:7878
- RADARR_API_KEY=api_key_radarr1|api_key_radarr2
- RADARR_CATEGORY=radarr-aMulerr|radarr-aMulerr-4k

# Two Sonarr instances (e.g. HD + 4K)
- SONARR_HOST=http://sonarr1:8989|http://sonarr2:8989
- SONARR_API_KEY=api_key_sonarr1|api_key_sonarr2
- SONARR_CATEGORY=tv-sonarr-aMulerr|tv-sonarr-aMulerr-4k
```

> [!IMPORTANT]
> All three variables for the same app (`HOST`, `API_KEY`, `CATEGORY`) must contain the exact same number of pipe-separated segments. A mismatch will cause the checker to exit with an error at startup.

> [!NOTE]
> Single-value configs (no pipe) work exactly as before — **full backward compatibility guaranteed**.

Each download is matched to its instance by **category**: when a download's aMulerr category matches a configured instance's category, all API calls (history lookup, monitoring checks, mark-as-failed) are routed to that specific instance automatically.

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
| `GHOST_LINK_STALL_CHECKS` | Checks before removing a **ghost link** (file never seen complete on the network, i.e., `last_seen_complete == 0`). Must be `>= 1` and `<= STALL_CHECKS`. | Same as `STALL_CHECKS` | ❌ No |

> [!NOTE]
> **Ghost link vs. stale source — two distinct thresholds:**
> - A **ghost link** (`last_seen_complete == 0`) is a file that has *never* been seen complete anywhere on the eMule network. It will never download. `GHOST_LINK_STALL_CHECKS` controls how quickly these are cleaned up.
> - A **stale source** (`last_seen_complete > 0` but older than `STALL_DAYS`) is a file that *did* exist at some point but whose sources have since dried up. It uses `STALL_CHECKS`, giving it more time in case sources reappear.
> - If `GHOST_LINK_STALL_CHECKS` is not set, it defaults to `STALL_CHECKS` — preserving identical behaviour for existing deployments.

#### *Arr Integration (at least one required)

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DOWNLOAD_CLIENT` | Download client name configured in Sonarr/Radarr | — | ✅ Yes |
| `RADARR_HOST` | Radarr base URL. Supports multiple instances via pipe-separated values: `http://host1:7878\|http://host2:7878` | `None` | ⚠️ Conditional |
| `RADARR_API_KEY` | Radarr API key. Must match the number of `RADARR_HOST` entries: `key1\|key2` | `None` | ⚠️ Conditional |
| `RADARR_CATEGORY` | aMulerr category for Radarr downloads. Must match the number of `RADARR_HOST` entries: `cat1\|cat2` | `None` | ⚠️ Conditional |
| `SONARR_HOST` | Sonarr base URL. Supports multiple instances via pipe-separated values: `http://host1:8989\|http://host2:8989` | `None` | ⚠️ Conditional |
| `SONARR_API_KEY` | Sonarr API key. Must match the number of `SONARR_HOST` entries: `key1\|key2` | `None` | ⚠️ Conditional |
| `SONARR_CATEGORY` | aMulerr category for Sonarr downloads. Must match the number of `SONARR_HOST` entries: `cat1\|cat2` | `None` | ⚠️ Conditional |

> [!NOTE]
> At least one of Radarr or Sonarr must be configured. All three variables for the same app must have the same number of pipe-separated entries.

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



