#!/usr/bin/env python3

import time
import sys
import os
import math
from datetime import datetime
from typing import List
import logging
from logging.handlers import RotatingFileHandler
from urllib3.util.retry import Retry
import requests
import apprise
from requests.adapters import HTTPAdapter
from requests import Session

# Sets the logging level based on the environment variable LOG_LEVEL
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
numeric_level = getattr(logging, log_level, None)
if not isinstance(numeric_level, int):
    raise ValueError(f'Invalid log level: {log_level}')

# Get the environment variable for the log file directly
log_to_file_path = os.getenv("LOG_TO_FILE", "")

# Configure the logger
logger = logging.getLogger(__name__)
logger.setLevel(numeric_level)

# Make sure there are no duplicate handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Log format
log_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Handler for the console
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_format)
logger.addHandler(console_handler)

# Handler for the file if specified
if log_to_file_path:
    try:
        # Make sure the directory exists
        os.makedirs(log_to_file_path, exist_ok=True)

        # Create the full path to the log file inside that directory
        log_file = os.path.join(log_to_file_path, "amulerr_stalled_checker.log")

        # Use this full path to the log file
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,  # 2 MB
            backupCount=6,
            encoding="utf-8"
        )
        file_handler.setFormatter(log_format)
        logger.addHandler(file_handler)
        logger.info("Log file configured in: %s", log_file)
    except Exception as e:
        logger.error("Log file configuration error: %s", e)

# ============= CUSTOM EXCEPTION =============
class ConnectionFailureException(Exception):
    """Raised when connection to Sonarr/Radarr fails"""
    pass

def _parse_pipe_instances(app_name: str) -> list[dict]:
    """
    Parse pipe-separated environment variables for a given *arr app (e.g. "RADARR" or "SONARR")
    into a list of instance dicts: [{"host": ..., "api_key": ..., "category": ...}, ...]

    Supports both single values (backward-compatible) and pipe-separated multi-instance values:
        RADARR_HOST=http://host1:7878|http://host2:7878
        RADARR_API_KEY=key1|key2
        RADARR_CATEGORY=category1|category2

    Returns an empty list when none of the vars are set.
    Exits with an error when the vars are set but mismatched in count or have empty segments.
    """
    prefix = app_name.upper()
    raw_host = os.environ.get(f'{prefix}_HOST', '')
    raw_key  = os.environ.get(f'{prefix}_API_KEY', '')
    raw_cat  = os.environ.get(f'{prefix}_CATEGORY', '')

    # If none are set, this app is not configured
    if not raw_host and not raw_key and not raw_cat:
        return []

    hosts = [h.strip() for h in raw_host.split('|')]
    keys  = [k.strip() for k in raw_key.split('|')]
    cats  = [c.strip() for c in raw_cat.split('|')]

    # All three must have the same number of segments
    if not (len(hosts) == len(keys) == len(cats)):
        logger.error(
            "%s_HOST, %s_API_KEY and %s_CATEGORY must contain the same number of "
            "pipe-separated values (got %s, %s, %s respectively).",
            prefix, prefix, prefix, len(hosts), len(keys), len(cats)
        )
        sys.exit(1)

    instances = []
    for idx, (host, key, cat) in enumerate(zip(hosts, keys, cats), start=1):
        label = f"{prefix} instance #{idx}"

        if not host or not key or not cat:
            logger.error("%s: host, api_key and category must all be non-empty.", label)
            sys.exit(1)

        if not host.startswith(('http://', 'https://')):
            logger.error("%s: host '%s' must start with 'http://' or 'https://'.", label, host)
            sys.exit(1)

        instances.append({"host": host, "api_key": key, "category": cat})
        logger.debug("Loaded %s → host=%s  category=%s", label, host, cat)

    return instances


class Config:
    # All environment variables must be provided by docker-compose.yml
    DRY_RUN = os.environ.get('DRY_RUN', 'false').lower() == 'true'  # flags for dry running

    AMULERR_ENDPOINT = '/api/v2/torrents/info'
    AMULERR_HOST = f"{os.environ.get('AMULERR_HOST', '')}"

    CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL'))  # in minutes
    STALL_CHECKS = int(os.environ.get('STALL_CHECKS'))  # number of checks before considering stall
    STALL_DAYS = int(os.environ.get('STALL_DAYS'))  # days after which a complete visa file is considered stalled
    RECENT_DOWNLOAD_GRACE_PERIOD = int(os.environ.get('RECENT_DOWNLOAD_GRACE_PERIOD', '30'))  # in minutes
    # Checks before removing a ghost link (last_seen_complete == 0). Defaults to STALL_CHECKS (opt-in).
    GHOST_LINK_STALL_CHECKS = int(os.environ.get('GHOST_LINK_STALL_CHECKS', STALL_CHECKS))

    # New configuration options for monitoring checks
    DELETE_IF_UNMONITORED_SERIE = os.environ.get('DELETE_IF_UNMONITORED_SERIE', 'false').lower() == 'true'
    DELETE_IF_UNMONITORED_SEASON = os.environ.get('DELETE_IF_UNMONITORED_SEASON', 'false').lower() == 'true'
    DELETE_IF_UNMONITORED_EPISODE = os.environ.get('DELETE_IF_UNMONITORED_EPISODE', 'false').lower() == 'true'

    DELETE_IF_UNMONITORED_MOVIE = os.environ.get('DELETE_IF_UNMONITORED_MOVIE', 'false').lower() == 'true'

    DELETE_IF_ONLY_ON_AMULERR = os.environ.get('DELETE_IF_ONLY_ON_AMULERR', 'false').lower() == 'true'

    # Download client name
    DOWNLOAD_CLIENT = os.environ.get('DOWNLOAD_CLIENT', '')  # download client name in Sonarr/Radarr

    # Multi-instance *arr configuration.
    # Each entry is a dict: {"host": str, "api_key": str, "category": str}
    # Parsed from pipe-separated env vars, e.g.:
    #   RADARR_HOST=http://host1:7878|http://host2:7878
    #   RADARR_API_KEY=key1|key2
    #   RADARR_CATEGORY=category1|category2
    RADARR_INSTANCES: list = []   # populated by validate()
    SONARR_INSTANCES: list = []   # populated by validate()

    # Notification configuration
    APPRISE_URLS = os.getenv('APPRISE_URLS', '')

    # Assigns API_URL directly in the body of the class
    API_URL = f"{os.environ.get('AMULERR_HOST', '')}{AMULERR_ENDPOINT}"

    @staticmethod
    def validate():
        mandatory_fields = [
            'CHECK_INTERVAL', 'API_URL', 'STALL_CHECKS', 'STALL_DAYS', 'DOWNLOAD_CLIENT', 'AMULERR_HOST'
        ]

        for field in mandatory_fields:
            value = getattr(Config, field)
            if value is None or value == '':
                logger.error("Environment variable %s must be set.", field)
                sys.exit(1)

        # Parse and validate per-app instances (supports single or pipe-separated multi-instance)
        Config.RADARR_INSTANCES = _parse_pipe_instances('RADARR')
        Config.SONARR_INSTANCES = _parse_pipe_instances('SONARR')

        if not Config.RADARR_INSTANCES and not Config.SONARR_INSTANCES:
            logger.error("At least one of RADARR_HOST or SONARR_HOST must be set.")
            sys.exit(1)

        if Config.RADARR_INSTANCES:
            logger.info("Radarr: %s instance(s) configured.", len(Config.RADARR_INSTANCES))
        if Config.SONARR_INSTANCES:
            logger.info("Sonarr: %s instance(s) configured.", len(Config.SONARR_INSTANCES))

        # Validate GHOST_LINK_STALL_CHECKS bounds
        if Config.GHOST_LINK_STALL_CHECKS < 1:
            logger.error(
                "GHOST_LINK_STALL_CHECKS must be >= 1 (got %s). "
                "A value of 0 would remove ghost links on the very first check with no warning.",
                Config.GHOST_LINK_STALL_CHECKS
            )
            sys.exit(1)

        if Config.GHOST_LINK_STALL_CHECKS > Config.STALL_CHECKS:
            logger.error(
                "GHOST_LINK_STALL_CHECKS (%s) must be <= STALL_CHECKS (%s). "
                "A ghost link cannot have a longer threshold than a normal stall.",
                Config.GHOST_LINK_STALL_CHECKS, Config.STALL_CHECKS
            )
            sys.exit(1)

        # Validate AMULERR_HOST format
        amulerr_host = os.environ.get('AMULERR_HOST')
        if amulerr_host and not amulerr_host.startswith(('http://', 'https://')):
            logger.error("Environment variable AMULERR_HOST must start with 'http://' or 'https://'.",)
            sys.exit(1)

    @staticmethod
    def get_notification_urls():
        """
        Get Apprise notification URLs.

        Priority:
        1. APPRISE_URLS if set

        Returns:
            list: List of Apprise-compatible notification URLs
        """
        urls = []

        if Config.APPRISE_URLS:
            urls.extend([
                u.strip() 
                for u in Config.APPRISE_URLS.replace(',', ' ').split() 
                if u.strip()
            ])
            logger.info(f"Using APPRISE_URLS: {len(urls)} notification service(s) configured")
            return urls

        return urls

class AmulerrDownload:
    def __init__(self, file_data: dict):
        self.name = file_data.get('name', '')
        self.hash = file_data.get('hash', '').upper()
        self.size = file_data.get('size', 0)
        self.size_done = file_data.get('downloaded', 0)             # aMulerr API key: 'downloaded'
        # Note: progress is a fraction (0 to 1.0) in the aMulerr API (qBittorrent-compatible format)
        # Multiply by 100 to get a percentage
        self.progress = file_data.get('progress', 0) * 100
        self.status = file_data.get('state', '')
        self.src_count = file_data.get('num_complete', 0)           # aMulerr API key: 'num_complete'
        self.src_count_a4af = file_data.get('num_seeds', 0)         # aMulerr API key: 'num_seeds'
        self.last_seen_complete = file_data.get('seen_complete', 0) # aMulerr API key: 'seen_complete'
        self.category = file_data.get('category', 'unknown')
        self.addedOn = file_data.get('added_on', 0) * 1000          # aMulerr API key: 'added_on' (seconds → ms)

    def __repr__(self):
        return (
            f"AmulerrDownload(name={self.name!r}, hash={self.hash!r}, size={self.size}, "
            f"size_done={self.size_done}, progress={self.progress}, status={self.status!r}, "
            f"src_count={self.src_count}, src_count_a4af={self.src_count_a4af}, "
            f"last_seen_complete={self.last_seen_complete}, category={self.category!r}, "
            f"addedOn={self.addedOn})"
        )

class SonarrDownload:
    def __init__(self, record_data: dict):
        self.title = record_data.get('sourceTitle', '')
        self.downloadId = record_data.get('downloadId', '').upper()
        self.download_client = record_data.get('downloadClientName', '')
        self.id = record_data.get('id', '')

        try:
            self.size = int(record_data.get('size', 0))
        except (ValueError, TypeError):
            self.size = 0

        self.series_id = record_data.get('seriesId', None)
        self.season_number = record_data.get('seasonNumber', None)
        self.episode_id = record_data.get('episodeId', None)

    def __repr__(self):
        return (
            f"SonarrDownload(title={self.title!r}, downloadId={self.downloadId!r}, "
            f"download_client={self.download_client!r}, id={self.id!r}, size={self.size}, "
            f"series_id={self.series_id!r}, season_number={self.season_number!r}, "
            f"episode_id={self.episode_id!r})"
        )

class RadarrDownload:
    def __init__(self, record_data: dict):
        self.title = record_data.get('sourceTitle', '')
        self.downloadId = record_data.get('downloadId', '').upper()
        self.download_client = record_data.get('downloadClientName', '')
        self.id = record_data.get('id', '')

        try:
            self.size = int(record_data.get('size', 0))
        except (ValueError, TypeError):
            self.size = 0

        self.movie_id = record_data.get('movieId', None)

    def __repr__(self):
        return (
            f"RadarrDownload(title={self.title!r}, downloadId={self.downloadId!r}, "
            f"download_client={self.download_client!r}, id={self.id!r}, size={self.size}, "
            f"movie_id={self.movie_id!r})"
        )

def check_special_cases(amulerr_data):
    """
    Processes the list of incomplete downloads:
      - For each download makes a paged request to the 'history' endpoint to get the records.
      - If no valid records (eventType "grabbed" and downloadClientName == Config.DOWNLOAD_CLIENT)
        is found, the download is considered present only on aMulerr and added to amulerr_downloads_to_remove.
      - If a valid record is present, an object is created (RadarrDownload or SonarrDownload)
        which also includes a reference to the original download and the valid record, and is added to the relevant queue.

      Next, for each object in the queues:
      - For Radarr: if the movie is not monitored (verified via is_movie_monitored), the original download
        is added to sonarr_radarr_downloads_to_remove.
      - For Sonarr: if the series, season or episode is not monitored (verified via respective functions),
        the original download is added to sonarr_radarr_downloads_to_remove.

      Returns a tuple with:
        (amulerr_downloads_to_remove, sonarr_radarr_downloads_to_remove, sonarr_queue, radarr_queue)
    """
    amulerr_downloads_to_remove = []
    sonarr_radarr_downloads_to_remove = []
    sonarr_queue = []
    radarr_queue = []

    def get_history_records(download, host, api_key, full_hash, page_size=10):
        headers = {
            "accept": "application/json",
            "X-Api-Key": api_key
        }
        all_records = []
        page = 1

        while True:
            history_url = f"{host}/api/v3/history?page={page}&pageSize={page_size}&downloadId={full_hash}"
            try:
                response = requests.get(history_url, headers=headers, timeout=10)
                
                if response.status_code != 200:
                    error_msg = f"HTTP {response.status_code} for '{download.name}' from {history_url}"
                    logger.error(error_msg)
                    raise ConnectionFailureException(error_msg)

                page_data = response.json()
                
            except requests.exceptions.Timeout:
                error_msg = f"Timeout connecting to {host} for '{download.name}'"
                logger.error(error_msg)
                raise ConnectionFailureException(error_msg)
                
            except requests.exceptions.ConnectionError:
                error_msg = f"Connection refused to {host} for '{download.name}'"
                logger.error(error_msg)
                raise ConnectionFailureException(error_msg)
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Request failed for '{download.name}': {e}"
                logger.error(error_msg)
                raise ConnectionFailureException(error_msg)
                
            except Exception as e:
                error_msg = f"Unexpected error during history request for '{download.name}': {e}"
                logger.error(error_msg)
                raise ConnectionFailureException(error_msg)

            records = page_data.get("records", [])
            all_records.extend(records)

            total_records = page_data.get("totalRecords", 0)
            total_pages = math.ceil(total_records / page_size)

            logger.debug("Page %s/%s for '%s', records obtained: %s", page, total_pages, download.name, len(records))

            if page >= total_pages:
                break
            page += 1

        return all_records

    def get_series_monitor_status(host, api_key, series_id):
        """Gets series monitoring status."""
        logger.debug("Getting series monitor status for series_id: %s", series_id)
        url = f"{host}/api/v3/series/{series_id}"
        headers = {"X-Api-Key": api_key}
        try:
            response = requests.get(url, headers=headers)
            logger.debug("Series API response status code: %s", response.status_code)
            if response.status_code == 200:
                series = response.json()
                logger.debug("Series monitored status: %s", series.get('monitored'))
                logger.debug("Number of seasons: %s", len(series.get('seasons', [])))
                return series.get('monitored', False), series.get('seasons', [])
            else:
                logger.error("Error in retrieving series information. Status code: %s", response.status_code)
                return False, []
        except Exception as e:
            logger.error("Error in retrieving series information: %s", e)
            return False, []

    def get_season_number_for_episode(sonarr_host, sonarr_api_key, episode_id):
        """
        Retrieve the season number of the episode using the Sonarr API.

        Args:
            sonarr_host (str): base URL of the Sonarr instance (e.g., "http://localhost:8989")
            sonarr_api_key (str): API Key for the Sonarr instance.
            episode_id (int): ID of the episode to be queried.

        Returns:
            int or None: The season number if found, otherwise None.
        """
        url = f"{sonarr_host}/api/v3/episode/{episode_id}"
        params = {
            "apikey": sonarr_api_key
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            season_number = data.get("seasonNumber")
            if season_number is None:
                logger.error("Season number not found for episode %s in the answer: %s", episode_id, data)
            else:
                logger.debug("Season number for the episode %s: %s", episode_id, season_number)
            return season_number

        except requests.RequestException as e:
            logger.error("Error when calling Sonarr for the episode. %s: %s", episode_id, e)
            return None

    def get_season_monitor_status(seasons, season_number):
        """Gets the status of monitoring the season."""
        logger.debug("Getting season monitor status for season_number: %s", season_number)
        for season in seasons:
            logger.debug("Checking season %s", season.get('seasonNumber'))
            if season.get('seasonNumber') == season_number:
                logger.debug("Season monitored status: %s", season.get('monitored'))
                return season.get('monitored', False)
        logger.debug("No season found with number %s", season_number)
        return False

    def get_episode_monitor_status(host, api_key, episode_id):
        """Gets the status of episode monitoring."""
        logger.debug("Getting episode monitor status for episode_id: %s", episode_id)
        url = f"{host}/api/v3/episode/{episode_id}"
        headers = {"X-Api-Key": api_key}
        try:
            response = requests.get(url, headers=headers)
            logger.debug("Episode API response status code: %s", response.status_code)
            if response.status_code == 200:
                episode = response.json()
                logger.debug("Episode monitored status: %s", episode.get('monitored'))
                return episode.get('monitored', False)
            else:
                logger.error("Error in retrieving episode information. Status code: %s", response.status_code)
                return False
        except Exception as e:
            logger.error("Error in retrieving episode information: %s", e)
            return False

    def is_movie_monitored(host, api_key, movie_id):
        """Check if the film is monitored."""
        logger.debug("Checking movie monitor status for movie_id: %s", movie_id)
        url = f"{host}/api/v3/movie/{movie_id}"
        headers = {"X-Api-Key": api_key}
        try:
            response = requests.get(url, headers=headers)
            logger.debug("Movie API response status code: %s", response.status_code)
            if response.status_code == 200:
                movie = response.json()
                logger.debug("Movie monitored status: %s", movie.get('monitored'))
                return movie.get('monitored', False)
            else:
                logger.error("Error in retrieving film information. Status Code: %s", response.status_code)
                return False
        except Exception as e:
            logger.error("Error in retrieving film information: %s", e)
            return False

    for download in amulerr_data:
        full_hash = download.hash + "00000000"

        client = None
        host = None
        api_key = None

        # Match download category against all configured Radarr instances
        matched_radarr = next(
            (inst for inst in Config.RADARR_INSTANCES if inst["category"] == download.category),
            None
        )
        # Match download category against all configured Sonarr instances
        matched_sonarr = next(
            (inst for inst in Config.SONARR_INSTANCES if inst["category"] == download.category),
            None
        )

        if matched_radarr:
            client = "radarr"
            host = matched_radarr["host"]
            api_key = matched_radarr["api_key"]
        elif matched_sonarr:
            client = "sonarr"
            host = matched_sonarr["host"]
            api_key = matched_sonarr["api_key"]
        else:
            all_categories = (
                [inst["category"] for inst in Config.RADARR_INSTANCES] +
                [inst["category"] for inst in Config.SONARR_INSTANCES]
            )
            logger.warning(
                "Category '%s' does not match any configured RADARR or SONARR category %s. "
                "Skip processing for download '%s'.",
                download.category, all_categories, download.name
            )
            continue

        try:
            logger.debug("[%s] Querying history from %s", download.name, client.upper())
            history_records = get_history_records(download, host, api_key, full_hash)
            logger.debug("[%s] Retrieved %s history records", download.name, len(history_records))
        except ConnectionFailureException as e:
            logger.error(f"🚨 Connection failure detected for '{download.name}': {e}")
            logger.warning(f"⚠️ Interrupting current check cycle. Will retry in {Config.CHECK_INTERVAL} minutes.")
            
            send_notification(
                f"⚠️ Connection failure to {client.upper()}\n"
                f"Download: {download.name}\n"
                f"Will retry in {Config.CHECK_INTERVAL} minutes",
                dry_run=Config.DRY_RUN
            )
            
            break

        valid_record = None
        for record in history_records:
            if record.get("eventType") != "grabbed":
                logger.debug("[%s] Ignored history record: eventType is '%s' (expected 'grabbed')", download.name, record.get("eventType"))
                continue
            data = record.get("data", {})
            download_client = str(data.get("downloadClientName", "")).lower()
            expected_client = str(Config.DOWNLOAD_CLIENT).lower()
            if download_client == expected_client:
                valid_record = record
                logger.debug("[%s] Found matching grabbed history record for client '%s'", download.name, download_client)
                break
            else:
                logger.debug("[%s] Ignored history record: downloadClientName is '%s' (expected '%s')", download.name, download_client, expected_client)

        if valid_record is None:
            if Config.DELETE_IF_ONLY_ON_AMULERR:
                logger.info(
                    f"Records present for '{download.name}' (hash: {download.hash}), but no one meets the criteria. "
                    "Download considered present only on aMulerr. Marking for removal."
                )
                amulerr_downloads_to_remove.append(download)
            else:
                logger.info(
                    f"Records present for '{download.name}' (hash: {download.hash}), but no one meets the criteria. "
                    "DELETE_IF_ONLY_ON_AMULERR is disabled, skipping removal."
                )
            continue

        if client == "radarr":
            r_download = RadarrDownload(valid_record)
            radarr_queue.append(r_download)
        elif client == "sonarr":
            s_download = SonarrDownload(valid_record)
            sonarr_queue.append(s_download)

    for r_obj in radarr_queue:
        movie_id = r_obj.movie_id

        # Resolve the Radarr instance that owns this download via its downloadId
        r_raw_id = r_obj.downloadId
        r_hash = r_raw_id[:-8] if r_raw_id.endswith("00000000") else r_raw_id
        radarr_inst = next(
            (inst for inst in Config.RADARR_INSTANCES
             if inst["category"] == next(
                 (d.category for d in amulerr_data if d.hash == r_hash), None
             )),
            Config.RADARR_INSTANCES[0] if Config.RADARR_INSTANCES else None
        )
        if radarr_inst is None:
            logger.error("[RADARR] Could not resolve instance for '%s'. Skipping.", r_obj.title)
            continue

        if not movie_id and Config.DELETE_IF_ONLY_ON_AMULERR:
            logger.info("The record '%s' does not contain 'movieId', it will only be considered on aMulerr.", r_obj.title)
            sonarr_radarr_downloads_to_remove.append(r_obj)
            continue

        logger.debug("[RADARR] Checking monitoring status for movie '%s' (movieId: %s)", r_obj.title, movie_id)
        if not is_movie_monitored(radarr_inst["host"], radarr_inst["api_key"], movie_id) and Config.DELETE_IF_UNMONITORED_MOVIE:
            logger.warning("[RADARR] The movie '%s' is not monitored. It will be marked for removal.", r_obj.title)
            sonarr_radarr_downloads_to_remove.append(r_obj)
        else:
            logger.debug("[RADARR] Movie '%s' is monitored or DELETE_IF_UNMONITORED_MOVIE is disabled.", r_obj.title)

    for s_obj in sonarr_queue:
        series_id = s_obj.series_id
        episode_id = s_obj.episode_id

        # Resolve the Sonarr instance that owns this download via its downloadId
        s_raw_id = s_obj.downloadId
        s_hash = s_raw_id[:-8] if s_raw_id.endswith("00000000") else s_raw_id
        sonarr_inst = next(
            (inst for inst in Config.SONARR_INSTANCES
             if inst["category"] == next(
                 (d.category for d in amulerr_data if d.hash == s_hash), None
             )),
            Config.SONARR_INSTANCES[0] if Config.SONARR_INSTANCES else None
        )
        if sonarr_inst is None:
            logger.error("[SONARR] Could not resolve instance for '%s'. Skipping.", s_obj.title)
            continue

        season_number = get_season_number_for_episode(sonarr_inst["host"], sonarr_inst["api_key"], episode_id)
        s_obj.season_number = season_number

        if not series_id and Config.DELETE_IF_ONLY_ON_AMULERR:
            logger.warning("The record '%s' does not contain 'seriesId', it will only be considered on aMulerr.", s_obj.title)
            sonarr_radarr_downloads_to_remove.append(s_obj)
            continue

        logger.debug("[SONARR] Checking monitoring status for seriesId: %s, episodeId: %s, season: %s", series_id, episode_id, season_number)
        series_monitored, seasons = get_series_monitor_status(sonarr_inst["host"], sonarr_inst["api_key"], series_id)
        if not series_monitored and Config.DELETE_IF_UNMONITORED_SERIE:
            logger.warning("[SONARR] The show '%s' is not monitored. It will be marked for removal.", s_obj.title)
            sonarr_radarr_downloads_to_remove.append(s_obj)
            continue
        else:
            logger.debug("[SONARR] Show '%s' series monitored: %s (DELETE_IF_UNMONITORED_SERIE: %s)", s_obj.title, series_monitored, Config.DELETE_IF_UNMONITORED_SERIE)

        if not episode_id and Config.DELETE_IF_ONLY_ON_AMULERR:
            logger.warning("The record '%s' does not contain 'episodeId', it will only be considered on aMulerr.", s_obj.title)
            sonarr_radarr_downloads_to_remove.append(s_obj)
            continue

        if season_number is None and Config.DELETE_IF_ONLY_ON_AMULERR:
            logger.warning("It was not possible to determine the season number for the episode %s.", episode_id)
            sonarr_radarr_downloads_to_remove.append(s_obj)
            continue

        if not get_season_monitor_status(seasons, season_number) and Config.DELETE_IF_UNMONITORED_SEASON:
            logger.warning("[SONARR] The season %s for '%s' is not monitored. It will be marked for removal.", season_number, s_obj.title)
            sonarr_radarr_downloads_to_remove.append(s_obj)
            continue
        else:
            logger.debug("[SONARR] Season %s for '%s' monitored or DELETE_IF_UNMONITORED_SEASON is disabled.", season_number, s_obj.title)

        if not get_episode_monitor_status(sonarr_inst["host"], sonarr_inst["api_key"], episode_id) and Config.DELETE_IF_UNMONITORED_EPISODE:
            logger.warning("[SONARR] The episode '%s' is not monitored. It will be marked for removal.", s_obj.title)
            sonarr_radarr_downloads_to_remove.append(s_obj)
        else:
            logger.debug("[SONARR] Episode '%s' (episodeId: %s) is monitored or DELETE_IF_UNMONITORED_EPISODE is disabled.", s_obj.title, episode_id)

    return amulerr_downloads_to_remove, sonarr_radarr_downloads_to_remove, sonarr_queue, radarr_queue

def amulerr_remove_download(hash_32: str, download_name: str, dry_run: bool = False):
    url = f"{Config.AMULERR_HOST}/api/v2/torrents/delete"
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {
        'hashes': hash_32.upper()
    }

    if not dry_run:
        try:
            response = requests.post(url, headers=headers, data=data)
            response.raise_for_status()
            logger.info("%s successfully removed from aMulerr.", download_name)
        except requests.exceptions.RequestException as e:
            logger.error("Error removing '%s': %s", download_name, e)
    else:
        logger.debug("DRY_RUN: Would remove %s from aMulerr.", download_name)

def handle_stalled_download(name: str, queue_id: str, host: str, api_key: str, dry_run: bool = True) -> bool:
    """
    Mark as failed a download (identified by queue_id) using the
    endpoint /api/v3/history/failed/{id}.

    :param name: The name of the download to be marked as failed.
    :param queue_id: The id of the download in the queue (Radarr/Sonarr) to be marked as failed.
    :param host: The base host (URL) of the service (Radarr or Sonarr).
    :param api_key: The API key for authentication.
    :param dry_run: If True, the function only logs the call without executing the action
    :return: True if the operation was successful, False otherwise
    """
    url = f"{host}/api/v3/history/failed/{queue_id}"
    headers = {
        'X-Api-Key': api_key,
        'Content-Type': 'application/json'
    }

    if dry_run:
        logger.info("[DRY RUN] I would mark as failed the download with id %s using: %s", name, url)
        return True

    try:
        response = requests.post(url, headers=headers)
        if response.status_code == 200:
            logger.info("%s -> Successfully marked as failed", name)
            return True
        else:
            logger.error("Error in marking as failed the download with id %s: status code %s, response: %s", name, response.status_code, response.text)
            return False
    except Exception as e:
        logger.exception("Exception in marking the download as failed %s: %s", name, e)
        return False


def send_notification(message: str, dry_run: bool = False, title: str = "aMulerr Stalled Checker"):
    """
    Send notification using Apprise.

    Supports 70+ notification services via Apprise.

    Args:
        message (str): Notification message body
        dry_run (bool): If True, log but don't send notification
        title (str): Notification title

    Returns:
        bool: True if notification sent successfully, False otherwise
    """
    if dry_run:
        logger.debug(f"[DRY RUN] Notification not sent: {message}")
        return True

    notification_urls = Config.get_notification_urls()
    
    if not notification_urls:
        logger.warning(
            "No notification service configured. "
            "Set APPRISE_URLS environment variable. "
            "Example: APPRISE_URLS=pover://user_key@app_token"
        )
        return False

    try:
        apobj = apprise.Apprise()
        
        added_count = 0
        for url in notification_urls:
            if apobj.add(url):
                added_count += 1
                logger.debug(f"Added notification service: {url[:20]}...")
            else:
                logger.warning(f"Failed to add invalid notification URL: {url[:30]}...")
        
        if added_count == 0:
            logger.error("No valid notification services could be added")
            return False
        
        logger.debug(f"Sending notification to {added_count} service(s)...")
        
        success = apobj.notify(
            body=message,
            title=title
        )
        
        if success:
            logger.info(f"Notification sent successfully to {added_count} service(s)")
            return True
        else:
            logger.error("Failed to send notification to one or more services")
            return False
            
    except Exception as e:
        logger.error(f"Error sending notification via Apprise: {str(e)}", exc_info=True)
        return False

class StallChecker:
    def __init__(self):
        self.warnings_file = ""
        if log_to_file_path:
            self.warnings_file = os.path.join(log_to_file_path, "warnings.json")
        self.warnings = self.load_warnings()
        self.previous_warnings = set()  # To keep track of downloads previously in warning
        self.previous_downloads = []    # Download history for future reference

    def load_warnings(self) -> dict:
        if self.warnings_file and os.path.exists(self.warnings_file):
            try:
                import json
                with open(self.warnings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info("Loaded %s warnings from persistent storage (%s)", len(data), self.warnings_file)
                    return data
            except Exception as e:
                logger.error("Error loading warnings file: %s", e)
        return {}

    def save_warnings(self):
        if self.warnings_file:
            try:
                import json
                with open(self.warnings_file, "w", encoding="utf-8") as f:
                    json.dump(self.warnings, f, indent=2, ensure_ascii=False)
                logger.debug("Saved warnings to persistent storage (%s)", self.warnings_file)
            except Exception as e:
                logger.error("Error saving warnings file: %s", e)

    def _tick_warning(self, current_hash: str, size_done: int, threshold: int, reason: str) -> tuple[bool, str, int]:
        """
        Increment (or initialise) the warning counter for `current_hash` and evaluate
        it against `threshold`.

        Returns: (is_stalled, reason, count)
          - is_stalled is True only when count > threshold.
          - reason is passed through unchanged for consistent log messages.
          - count is the current warning tally after incrementing.
          - The first call for a new hash always returns (False, reason, 1),
            ensuring a download is never removed without at least one prior warning.
        """
        if current_hash in self.warnings:
            self.warnings[current_hash]['count'] += 1
            self.warnings[current_hash]['last_size'] = size_done
            count = self.warnings[current_hash]['count']
        else:
            self.warnings[current_hash] = {'count': 1, 'last_size': size_done}
            count = 1

        is_stalled = count > threshold
        self.save_warnings()
        return is_stalled, reason, count

    def check_status(self, download: AmulerrDownload) -> tuple[bool, str, int]:
        current_hash = download.hash

        added_on = download.addedOn / 1000  # Convert to seconds
        recent_download_threshold = time.time() - (Config.RECENT_DOWNLOAD_GRACE_PERIOD * 60)
        
        logger.debug("[%s] Check status evaluating:", download.name)
        logger.debug("  - Added on: %s", datetime.fromtimestamp(added_on).strftime('%Y-%m-%d %H:%M:%S'))
        logger.debug("  - Grace period threshold: %s (grace period: %s min)", datetime.fromtimestamp(recent_download_threshold).strftime('%Y-%m-%d %H:%M:%S'), Config.RECENT_DOWNLOAD_GRACE_PERIOD)
        
        if added_on > recent_download_threshold:
            logger.debug(
                "  -> MATCH: Download is within the grace period (added %s minutes ago, grace period is %s minutes). Skipping further checks.",
                round((time.time() - added_on) / 60, 2),
                Config.RECENT_DOWNLOAD_GRACE_PERIOD
            )
            if current_hash in self.warnings:
                del self.warnings[current_hash]
                self.save_warnings()
            return False, "", 0
        else:
            logger.debug("  -> SKIP: Download is older than the grace period.")

        # Check if src_count_a4af > 0 (sources available on other files — download is queued)
        logger.debug("  - Queue check: src_count_a4af = %s", download.src_count_a4af)
        if download.src_count_a4af > 0:
            logger.debug(
                "  -> MATCH: Download has sources queued on other files (src_count_a4af: %s > 0). Skipping further checks.",
                download.src_count_a4af
            )
            if current_hash in self.warnings:
                del self.warnings[current_hash]
                self.save_warnings()
            return False, "", 0
        else:
            logger.debug("  -> SKIP: No sources queued on other files (src_count_a4af <= 0).")

        # Check if download is 100% complete
        logger.debug("  - Progress check: progress = %s%%", download.progress)
        if download.progress >= 100:
            logger.debug("  -> MATCH: Download is 100%% complete. Skipping further checks.")
            if current_hash in self.warnings:
                del self.warnings[current_hash]
                self.save_warnings()
            return False, "", 0
        else:
            logger.debug("  -> SKIP: Download is not complete (progress < 100%%).")

        # Check if size_done has changed since last check
        last_size = self.warnings[current_hash]['last_size'] if current_hash in self.warnings else None
        logger.debug("  - Size progress check: current size_done = %s, last checked size = %s", download.size_done, last_size)
        if current_hash in self.warnings and download.size_done != last_size:
            logger.debug(
                "  -> MATCH: Progress detected! size_done changed from %s to %s. Clearing warning counter.",
                last_size,
                download.size_done
            )
            del self.warnings[current_hash]
            self.save_warnings()
            return False, "", 0
        else:
            if current_hash in self.warnings:
                logger.debug("  -> SKIP: Size has not changed since last check (still %s).", download.size_done)
            else:
                logger.debug("  -> SKIP: No previous warnings recorded for this file.")

        # Ghost link: file has NEVER been seen complete on the network.
        # Uses the dedicated GHOST_LINK_STALL_CHECKS threshold (always <= STALL_CHECKS).
        logger.debug("  - Ghost link check: last_seen_complete = %s", download.last_seen_complete)
        if download.last_seen_complete == 0:
            is_stalled, reason, count = self._tick_warning(
                current_hash, download.size_done,
                Config.GHOST_LINK_STALL_CHECKS,
                "Never seen complete on network (ghost link)"
            )
            logger.debug(
                "  -> MATCH: Ghost link detected. Ticking warning: count=%s/%s, is_stalled=%s, reason='%s'",
                count, Config.GHOST_LINK_STALL_CHECKS, is_stalled, reason
            )
            return is_stalled, reason, count
        else:
            logger.debug("  -> SKIP: File has been seen complete on network at least once.")

        # Stale source: was seen complete at some point, but too long ago to be reliable.
        # Uses the standard STALL_CHECKS threshold.
        stall_time = time.time() - (Config.STALL_DAYS * 24 * 60 * 60)
        last_seen_str = datetime.fromtimestamp(download.last_seen_complete).strftime('%Y-%m-%d %H:%M:%S')
        stall_threshold_str = datetime.fromtimestamp(stall_time).strftime('%Y-%m-%d %H:%M:%S')
        logger.debug("  - Stale source check: last_seen_complete = %s, stall threshold = %s", last_seen_str, stall_threshold_str)
        if download.last_seen_complete < stall_time:
            last_seen_days = round((time.time() - download.last_seen_complete) / (24 * 60 * 60), 2)
            is_stalled, reason, count = self._tick_warning(
                current_hash, download.size_done,
                Config.STALL_CHECKS,
                f"Last seen complete > {Config.STALL_DAYS} days ago (actually {last_seen_days} days ago)"
            )
            logger.debug(
                "  -> MATCH: Stale source detected. Ticking warning: count=%s/%s, is_stalled=%s, reason='%s'",
                count, Config.STALL_CHECKS, is_stalled, reason
            )
            return is_stalled, reason, count
        else:
            logger.debug("  -> SKIP: Last seen complete is within the allowed timeframe.")

        # Source was seen complete recently — download is healthy; clear any warning.
        last_seen_days = round((time.time() - download.last_seen_complete) / (24 * 60 * 60), 2)
        logger.debug(
            "  -> Download is healthy: last seen complete was recently (%s days ago). Clearing warnings if any exist.",
            last_seen_days
        )
        if current_hash in self.warnings:
            del self.warnings[current_hash]
            self.save_warnings()
        return False, "", 0

    def cleanup_warnings(self, current_hashes: set[str], downloads_map: dict):
        if not hasattr(self, 'hash_to_name_map'):
            self.hash_to_name_map = {}

        if not hasattr(self, 'stalled_hashes'):
            self.stalled_hashes = set()

        for download in self.previous_downloads:
            self.hash_to_name_map[download.hash] = download.name

        for hash_key, download in downloads_map.items():
            self.hash_to_name_map[hash_key] = download.name

        to_remove = [h for h in self.warnings.keys() if h not in current_hashes]
        for h in to_remove:
            if h in self.stalled_hashes:
                del self.warnings[h]
                continue

            if h in downloads_map:
                logger.info("Download '%s' removed from monitoring (no longer on download list)", downloads_map[h].name)
            elif h in self.hash_to_name_map:
                logger.info("Download '%s' removed from monitoring (no longer on download list)", self.hash_to_name_map[h])
            else:
                logger.info("Download with hash %s... removed from monitoring (no longer on download list)", h[:8])
            del self.warnings[h]

        if to_remove:
            self.save_warnings()

        self.previous_warnings = self.previous_warnings.intersection(current_hashes)
        self.previous_downloads = list(downloads_map.values())

def fetch_amulerr_data() -> List[AmulerrDownload]:
    """Retrieve active downloads from server with retry mechanism, filtering by SONARR_CATEGORY or RADARR_CATEGORY"""
    session = Session()
    retry_strategy = Retry(
        total=10,
        backoff_factor=30,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        response = session.get(Config.API_URL)
        response.raise_for_status()

        files = response.json()  # aMulerr info returns a list directly
        logger.debug("Retrieved %s total file%s", len(files), 's' if len(files) != 1 else '')

        for file in files:
            category = file.get('category', 'Category not found')
            logger.debug("File category: %s", category)

        # Build the set of all valid categories across every configured instance
        valid_categories = (
            {inst["category"] for inst in Config.RADARR_INSTANCES} |
            {inst["category"] for inst in Config.SONARR_INSTANCES}
        )
        filtered_downloads = [
            AmulerrDownload(file) for file in files
            if file.get('category') in valid_categories
        ]

        return filtered_downloads
    except requests.exceptions.RequestException as e:
        logger.error("Error retrieving downloads: %s", e)
        return []

def main():
    stall_checker = StallChecker()

    logger.info("=== Configuration Summary ===")
    for attr, value in Config.__dict__.items():
        if not callable(value) and not attr.startswith("__"):
            logger.info("%s: %s", attr, value)
    logger.info("=== Configuration Summary ===")

    while True:
        try:
            amulerr_data = fetch_amulerr_data()

            amulerr_downloads_to_remove, sonarr_radarr_downloads_to_remove, sonarr_queue, radarr_queue = check_special_cases(amulerr_data)

            for download in amulerr_downloads_to_remove + sonarr_radarr_downloads_to_remove:
                if isinstance(download, AmulerrDownload):
                    identifier = download.hash
                    name = download.name
                elif isinstance(download, (SonarrDownload, RadarrDownload)):
                    raw_id = download.downloadId
                    identifier = raw_id[:-8] if raw_id.endswith("00000000") else raw_id
                    name = download.title
                else:
                    logger.debug("Download type not recognized: %s", download)
                    continue

                logger.debug("Removal in progress for: %s, identifier: %s", name, identifier)

                amulerr_remove_download(identifier, name, Config.DRY_RUN)

                if identifier in stall_checker.warnings:
                    del stall_checker.warnings[identifier]
                    stall_checker.save_warnings()

                if identifier in stall_checker.previous_warnings:
                    stall_checker.previous_warnings.remove(identifier)

                if isinstance(download, AmulerrDownload):
                    try:
                        amulerr_data.remove(download)
                        logger.debug("Removed correctly %s (AmulerrDownload) from amulerr_data.", name)
                    except ValueError:
                        logger.error("Unable to remove %s from amulerr_data.", name)
                elif isinstance(download, (SonarrDownload, RadarrDownload)):
                    candidate = next(
                        (d for d in amulerr_data if isinstance(d, AmulerrDownload) and d.hash == identifier),
                        None
                    )
                    if candidate:
                        try:
                            amulerr_data.remove(candidate)
                            logger.debug("Removed correctly %s (found AmulerrDownload) from amulerr_data.", name)
                        except ValueError:
                            logger.error("Error in removing %s from amulerr_data, candidate found: %s", name, candidate)
                    else:
                        logger.error("Unable to remove %s from amulerr_data: no AmulerrDownload found with hash %s.", name, identifier)

            incomplete_downloads = [d for d in amulerr_data if d.progress < 100]
            completed_downloads = [d for d in amulerr_data if d.progress == 100]

            stall_checker.previous_downloads = amulerr_data

            if incomplete_downloads:
                download_states = {}
                stalled_downloads = []
                warning_downloads = []
                current_warning_hashes = set()

                logger.debug("\nChecking %s incomplete file%s", len(incomplete_downloads), 's' if len(incomplete_downloads) != 1 else '')

                downloads_map = {d.hash: d for d in incomplete_downloads}
                current_hashes = set(downloads_map.keys())

                stall_checker.cleanup_warnings(current_hashes, downloads_map)

                for index, download in enumerate(incomplete_downloads):
                    if logger.getEffectiveLevel() == logging.DEBUG and index > 0:
                        logger.debug("")
                    is_stalled, stall_reason, check_count = stall_checker.check_status(download)
                    download_states[download.hash] = (is_stalled, stall_reason, check_count)

                if logger.getEffectiveLevel() == logging.DEBUG:
                    for download in incomplete_downloads:
                        is_stalled, stall_reason, check_count = download_states[download.hash]
                        status = f"STALLED: {stall_reason}" if is_stalled else "Active"

                        last_seen = "Never" if download.last_seen_complete == 0 else \
                            datetime.fromtimestamp(download.last_seen_complete).strftime('%Y-%m-%d %H:%M:%S')
                        logger.debug("Download: %s, Status: %s, Last Seen Complete: %s, Check Count: %s", download.name, status, last_seen, check_count)

                for download in incomplete_downloads:
                    is_stalled, stall_reason, check_count = download_states[download.hash]

                    if is_stalled or check_count > Config.STALL_CHECKS:
                        stalled_downloads.append((download, check_count, stall_reason or "Max checks reached"))
                    elif check_count > 0:
                        warning_downloads.append((download, check_count, stall_reason or "Approaching stall threshold"))

                if warning_downloads:
                    logger.debug("Warning downloads (%s/%s):", len(warning_downloads), len(incomplete_downloads))
                    for download, count, warning_reason in warning_downloads:
                        logger.info("%s -> Warning (%s/%s) - %s", download.name, count, Config.STALL_CHECKS, warning_reason)
                        current_warning_hashes.add(download.hash)
                else:
                    logger.debug("No warning downloads")

                if stalled_downloads:
                    logger.debug("Stalled downloads (%s/%s):", len(stalled_downloads), len(incomplete_downloads))
                    for download, check_count, stall_reason in stalled_downloads:
                        logger.info("%s -> Stalled (%s/%s warnings) - %s", download.name, check_count, Config.STALL_CHECKS, stall_reason)

                        send_notification(f"Download {download.name} marked as stalled: {stall_reason}. Will be removed", dry_run=Config.DRY_RUN)

                        matched_radarr_inst = next(
                            (inst for inst in Config.RADARR_INSTANCES if inst["category"] == download.category),
                            None
                        )
                        matched_sonarr_inst = next(
                            (inst for inst in Config.SONARR_INSTANCES if inst["category"] == download.category),
                            None
                        )

                        if matched_radarr_inst:
                            host = matched_radarr_inst["host"]
                            api_key = matched_radarr_inst["api_key"]
                            matching_item = next(
                                (item for item in radarr_queue
                                 if (item.downloadId[:-8] == download.hash)),
                                None
                            )
                        elif matched_sonarr_inst:
                            host = matched_sonarr_inst["host"]
                            api_key = matched_sonarr_inst["api_key"]
                            matching_item = next(
                                (item for item in sonarr_queue
                                 if (item.downloadId[:-8] == download.hash)),
                                None
                            )
                        else:
                            logger.debug("Category not recognized for %s: %s", download.name, download.category)
                            continue

                        if not matching_item:
                            logger.error("Queue item not found for %s (hash: %s)", download.name, download.hash)
                            return

                        queue_id = matching_item.id

                        amulerr_remove_download(download.hash, download.name, Config.DRY_RUN)

                        if download.hash in stall_checker.previous_warnings:
                            stall_checker.previous_warnings.remove(download.hash)

                        if download.hash in current_warning_hashes:
                            current_warning_hashes.remove(download.hash)

                        if not hasattr(stall_checker, 'stalled_hashes'):
                            stall_checker.stalled_hashes = set()
                        stall_checker.stalled_hashes.add(download.hash)

                        handle_stalled_download(download.name, queue_id, host, api_key, Config.DRY_RUN)

                else:
                    logger.debug("No stalled downloads")

                resolved_warnings = stall_checker.previous_warnings - current_warning_hashes
                for hash_value in resolved_warnings:
                    matching_download = next((d for d in incomplete_downloads if d.hash == hash_value), None)
                    if matching_download:
                        logger.info("%s -> No longer in warning state", matching_download.name)

                stall_checker.previous_warnings = current_warning_hashes

            else:
                logger.debug("No incomplete downloads to check.")

            if completed_downloads:
                logger.debug("Checking %s completed file%s", len(completed_downloads), 's' if len(completed_downloads) != 1 else '')
                for download in completed_downloads:
                    logger.debug("Completed download: %s", download.name)
            else:
                logger.debug("No completed downloads to check.")

            logger.debug("Waiting %s minute(s) before next check...", Config.CHECK_INTERVAL)
            time.sleep(Config.CHECK_INTERVAL * 60)

        except KeyboardInterrupt:
            logger.debug("Interrupted by user")
            break
        except Exception as e:
            logger.error("Error in main loop: %s", e)
            time.sleep(Config.CHECK_INTERVAL * 60)

if __name__ == "__main__":
    try:
        Config.validate()
        main()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)