"""Provider abstraction. ApiFootball is the first implementation.
The key is read from the API_FOOTBALL_KEY environment variable only."""
import os, time
from abc import ABC, abstractmethod
import httpx

BASE = "https://v3.football.api-sports.io"
STATE = {"remaining": None, "last_error": None, "last_fixture_sync": None, "last_live_sync": None, "season": None}
_cache: dict = {}

class ProviderError(Exception):
    pass

def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val

class FootballProvider(ABC):
    @abstractmethod
    def fixtures(self, league_id: int, season: int) -> list[dict]: ...
    @abstractmethod
    def live(self, league_ids: list[int]) -> list[dict]: ...
    @abstractmethod
    def fixture(self, fixture_id: int) -> list[dict]: ...
    @abstractmethod
    def events(self, fixture_id: int) -> list[dict]: ...
    @abstractmethod
    def lineups(self, fixture_id: int) -> list[dict]: ...
    @abstractmethod
    def statistics(self, fixture_id: int) -> list[dict]: ...
    @abstractmethod
    def head_to_head(self, team_a: int, team_b: int) -> list[dict]: ...

class ApiFootball(FootballProvider):
    def _get(self, path: str, **params):
        key = os.environ.get("API_FOOTBALL_KEY", "").strip()
        if not key:
            raise ProviderError("API_FOOTBALL_KEY is not set")
        try:
            r = httpx.get(BASE + path, params=params, headers={"x-apisports-key": key}, timeout=20)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"HTTP error: {e.__class__.__name__}")
        STATE["remaining"] = r.headers.get("x-ratelimit-requests-remaining")
        body = r.json()
        if body.get("errors"):  # the API reports problems inside a 200 response
            raise ProviderError(str(body["errors"]))
        return body.get("response", [])

    def fixtures(self, league_id, season): return self._get("/fixtures", league=league_id, season=season)
    def live(self, league_ids): return self._get("/fixtures", live="-".join(map(str, league_ids)))
    def fixture(self, fixture_id): return self._get("/fixtures", id=fixture_id)
    def events(self, fixture_id): return self._get("/fixtures/events", fixture=fixture_id)
    def lineups(self, fixture_id): return self._get("/fixtures/lineups", fixture=fixture_id)
    def statistics(self, fixture_id): return self._get("/fixtures/statistics", fixture=fixture_id)
    def head_to_head(self, a, b): return self._get("/fixtures/headtohead", h2h=f"{a}-{b}", last=5)
