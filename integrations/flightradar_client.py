from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


class FlightradarError(RuntimeError):
    """Raised when Flightradar24 returns an error response."""


@dataclass
class FlightradarConfig:
    api_key: str
    api_base_url: str = 'https://fr24api.flightradar24.com'
    accept_version: str = 'v1'
    timeout: int = 30


class FlightradarClient:
    def __init__(self, config: FlightradarConfig, *, session: Optional[requests.Session] = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def get_usage(self, *, period: str = '24h') -> Dict[str, Any]:
        return self._request('GET', '/api/usage', params={'period': period})

    def get_airline_light(self, icao: str) -> Dict[str, Any]:
        return self._request('GET', f'/api/static/airlines/{icao.strip().upper()}/light')

    def get_live_positions_full(
        self,
        *,
        operating_as: Optional[str] = None,
        painted_as: Optional[str] = None,
        bounds: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {'limit': limit}
        if operating_as:
            params['operating_as'] = operating_as
        if painted_as:
            params['painted_as'] = painted_as
        if bounds:
            params['bounds'] = bounds
        return self._request('GET', '/api/live/flight-positions/full', params=params)

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.config.api_key:
            raise FlightradarError('FLIGHTRADAR_API_KEY is not configured.')

        headers = {
            'Accept-Version': self.config.accept_version,
            'Authorization': f'Bearer {self.config.api_key}',
        }
        response = self.session.request(
            method,
            f"{self.config.api_base_url.rstrip('/')}/{path.lstrip('/')}",
            headers=headers,
            params=params,
            timeout=self.config.timeout,
        )
        if response.status_code >= 400:
            raise FlightradarError(
                f'Flightradar24 {method} {path} failed: {response.status_code} {self._safe_error(response)}'
            )
        if not response.content:
            return {}
        return response.json()

    @staticmethod
    def _safe_error(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:500]

        message = body.get('message') if isinstance(body, dict) else None
        details = body.get('details') if isinstance(body, dict) else None
        if message and details:
            return f'{message}: {details}'
        if message:
            return str(message)
        return str(body)[:500]
