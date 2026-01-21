import base64
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL = "https://play.qobuz.com"
_APP_ID_REGEX = re.compile(r'production:{api:{appId:"(?P<app_id>\\d{9})",appSecret:"\\w{32}"')
_BUNDLE_URL_REGEX = re.compile(r'<script src="(/resources/\\d+\\.\\d+\\.\\d+-[a-z]\\d{3}/bundle\\.js)"></script>')
_SEED_TIMEZONE_REGEX = re.compile(r'[a-z]\\.initialSeed\("(?P<seed>[\\w=]+)",window\\.utimezone\\.(?P<timezone>[a-z]+)\)')
_INFO_EXTRAS_REGEX = r'name:"\\w+/(?P<timezone>{timezones})",info:"(?P<info>[\\w=]+)",extras:"(?P<extras>[\\w=]+)"'

class QobuzClient:
    def __init__(self, email=None, password=None, token=None, app_id=None, app_secret=None):
        self.email = email
        self.password = password
        self.user_auth_token = token
        self.app_id = app_id
        self.active_secret = app_secret
        self.secrets = [app_secret] if app_secret else []
        self.base_url = "https://www.qobuz.com/api.json/0.2/"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0",
            "Content-Type": "application/json;charset=UTF-8"
        }
        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    async def initialize(self):
        """Initialize headers and login."""
        if not self.app_id or not self.active_secret:
            await self._scrape_bundle()
        else:
            self.client.headers["X-App-Id"] = self.app_id
            logger.info(f"Using provided App ID: {self.app_id}")

        if self.user_auth_token:
            self.client.headers["X-User-Auth-Token"] = self.user_auth_token
            logger.info("Using provided user auth token")
            # If app_secret was NOT provided, we need to find it from the scraped bundle
            if not self.active_secret:
                await self._find_active_secret()
        elif self.email and self.password:
            await self.login(self.email, self.password)
        else:
            # If no login, we still need a secret for some calls
            if not self.active_secret:
                await self._find_active_secret()
        return True

    async def _scrape_bundle(self):
        logger.info("Scraping Qobuz bundle for App ID and Secrets...")
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{_BASE_URL}/login")
            resp.raise_for_status()
            
            bundle_url_match = _BUNDLE_URL_REGEX.search(resp.text)
            if not bundle_url_match:
                raise Exception("Could not find bundle URL")
            
            bundle_url = _BASE_URL + bundle_url_match.group(1)
            resp = await client.get(bundle_url)
            resp.raise_for_status()
            bundle_js = resp.text

            # Get App ID
            app_id_match = _APP_ID_REGEX.search(bundle_js)
            if not app_id_match:
                raise Exception("Could not find App ID in bundle")
            self.app_id = app_id_match.group("app_id")
            
            # Get Secrets
            seed_matches = _SEED_TIMEZONE_REGEX.finditer(bundle_js)
            secrets_raw = OrderedDict()
            for match in seed_matches:
                seed, timezone = match.group("seed", "timezone")
                secrets_raw[timezone] = [seed]

            keypairs = list(secrets_raw.items())
            if len(keypairs) > 1:
                secrets_raw.move_to_end(keypairs[1][0], last=False)
            
            timezones_pattern = "|".join([tz.capitalize() for tz in secrets_raw])
            info_extras_regex = _INFO_EXTRAS_REGEX.format(timezones=timezones_pattern)
            info_extras_matches = re.finditer(info_extras_regex, bundle_js)
            
            for match in info_extras_matches:
                timezone, info, extras = match.group("timezone", "info", "extras")
                secrets_raw[timezone.lower()] += [info, extras]
            
            self.secrets = []
            for tz in secrets_raw:
                try:
                    decoded = base64.standard_b64decode("".join(secrets_raw[tz])[:-44]).decode("utf-8")
                    self.secrets.append(decoded)
                except:
                    continue
            
            self.client.headers["X-App-Id"] = self.app_id
            logger.info(f"Initialized with App ID: {self.app_id}")

    async def login(self, email, password):
        params = {
            "email": email,
            "password": password,
            "app_id": self.app_id
        }
        resp = await self.client.get(f"{self.base_url}user/login", params=params)
        data = resp.json()
        if "user_auth_token" not in data:
            raise Exception(f"Login failed: {data.get('error', 'Unknown error')}")
        
        self.user_auth_token = data["user_auth_token"]
        self.client.headers["X-User-Auth-Token"] = self.user_auth_token
        logger.info("Login successful")
        await self._find_active_secret()

    async def _find_active_secret(self):
        # Test secrets against a known track ID to find the working one
        test_track_id = 5966783 # random track id
        for secret in self.secrets:
            if not secret: continue
            try:
                sig = self._generate_sig("track", "getFileUrl", {"track_id": test_track_id, "format_id": 5}, secret)
                params = {
                    "request_ts": sig["ts"],
                    "request_sig": sig["sig"],
                    "track_id": test_track_id,
                    "format_id": 5,
                    "intent": "stream"
                }
                resp = await self.client.get(f"{self.base_url}track/getFileUrl", params=params)
                # 200 = Success
                # 403 = Forbidden (but secret is valid, just not allowed for this track/user)
                # 401 = Unauthorized (token is bad, but secret might be fine - hard to say)
                # 400 = Bad Request (often means Invalid App Secret)
                if resp.status_code in [200, 403]:
                    self.active_secret = secret
                    logger.info("Found active secret")
                    return
                elif resp.status_code == 401:
                    logger.warning("Token unauthorized during secret check. Secret might be valid but token is rejected.")
                    # If we have a token, and it's 401, we might want to continue or fail
            except:
                continue
        raise Exception("Could not find a valid app secret")

    def _generate_sig(self, entity, method, params, secret):
        ts = int(time.time())
        # Example sig format: trackgetFileUrlformat_id5intentstreamtrack_id5966783<ts><secret>
        if method == "getFileUrl":
            r_sig = f"trackgetFileUrlformat_id{params['format_id']}intentstreamtrack_id{params['track_id']}{ts}{secret}"
        elif method == "getUserFavorites":
            r_sig = f"favoritegetUserFavorites{ts}{secret}"
        else:
            r_sig = f"{entity}{method}{ts}{secret}"
            
        r_sig_hashed = hashlib.md5(r_sig.encode("utf-8")).hexdigest()
        return {"ts": ts, "sig": r_sig_hashed}

    async def request(self, endpoint, params=None):
        resp = await self.client.get(f"{self.base_url}{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def search(self, query, type="album", limit=20, offset=0):
        # type: album, artist, track, playlist
        params = {"query": query, "limit": limit, "offset": offset}
        endpoint = f"{type}/search"
        return await self.request(endpoint, params)

    async def get_album(self, album_id):
        return await self.request("album/get", {"album_id": album_id})

    async def get_track(self, track_id):
        return await self.request("track/get", {"track_id": track_id})

    async def get_artist(self, artist_id):
        return await self.request("artist/get", {"artist_id": artist_id})

    async def get_artist_releases(self, artist_id, release_type="album", limit=20, offset=0):
        # release_type: album, live, compilation, epSingle
        params = {
            "artist_id": artist_id, 
            "release_type": release_type,
            "limit": limit, 
            "offset": offset,
            "sort": "release_date"
        }
        return await self.request("artist/getReleasesList", params)

    async def get_file_url(self, track_id, format_id=6):
        sig_data = self._generate_sig("track", "getFileUrl", {"track_id": track_id, "format_id": format_id}, self.active_secret)
        params = {
            "request_ts": sig_data["ts"],
            "request_sig": sig_data["sig"],
            "track_id": track_id,
            "format_id": format_id,
            "intent": "stream"
        }
        return await self.request("track/getFileUrl", params)

    async def close(self):
        await self.client.aclose()
