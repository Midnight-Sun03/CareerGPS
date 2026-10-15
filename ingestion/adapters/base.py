import time
from collections import defaultdict
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SourceAccessDenied(RuntimeError):
    pass


class PoliteHttpClient:
    def __init__(self, delay=None, timeout=None):
        self.delay = settings.INGESTION_REQUEST_DELAY_SECONDS if delay is None else delay
        self.timeout = settings.INGESTION_TIMEOUT_SECONDS if timeout is None else timeout
        self.user_agent = settings.INGESTION_USER_AGENT
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent, "Accept": "text/html,application/json"})
        retry = Retry(
            total=3, connect=3, read=3, backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}), respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4))
        self.session.mount("http://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4))
        self._last_request = defaultdict(float)
        self._robots = {}

    def _robots_parser(self, url):
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root in self._robots:
            return self._robots[root]
        parser = RobotFileParser()
        robots_url = urljoin(root, "/robots.txt")
        try:
            response = self.session.get(robots_url, timeout=self.timeout)
            if response.status_code in (401, 403):
                parser.parse(["User-agent: *", "Disallow: /"])
            elif response.ok:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])
        except requests.RequestException:
            parser.parse([])
        self._robots[root] = parser
        return parser

    def _prepare_request(self, url, respect_robots):
        if respect_robots and not self._robots_parser(url).can_fetch(self.user_agent, url):
            raise SourceAccessDenied(f"robots.txt does not allow retrieval of {url}")
        host = urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last_request[host])
        if wait > 0:
            time.sleep(wait)
        return host

    def get(self, url, *, params=None, respect_robots=True, headers=None):
        host = self._prepare_request(url, respect_robots)
        response = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
        self._last_request[host] = time.monotonic()
        response.raise_for_status()
        return response

    def post(self, url, *, data=None, respect_robots=True, headers=None):
        """POST only when a source's public listing pagination requires it.

        Adapters must never use this helper to submit applications.
        """
        host = self._prepare_request(url, respect_robots)
        response = self.session.post(url, data=data, timeout=self.timeout, headers=headers)
        self._last_request[host] = time.monotonic()
        response.raise_for_status()
        return response

    def post_json(self, url, *, payload=None, respect_robots=True, headers=None):
        """POST a JSON body to a public catalogue endpoint that paginates via POST.

        Adapters must never use this helper to submit applications.
        """
        merged = {"Accept": "application/json", "Content-Type": "application/json"}
        if headers:
            merged.update(headers)
        host = self._prepare_request(url, respect_robots)
        response = self.session.post(url, json=payload or {}, timeout=self.timeout, headers=merged)
        self._last_request[host] = time.monotonic()
        response.raise_for_status()
        return response

    def get_json(self, url, *, params=None, respect_robots=True, headers=None):
        merged = {"Accept": "application/json"}
        if headers:
            merged.update(headers)
        response = self.get(
            url, params=params, respect_robots=respect_robots, headers=merged
        )
        return response.json()
