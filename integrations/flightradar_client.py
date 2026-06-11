from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


class FlightradarError(RuntimeError):
    """Raised when Flightradar24 returns an error response."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, reason: str = 'api_error') -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


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

    def get_flight_summary_full(
        self,
        *,
        flight_datetime_from: str,
        flight_datetime_to: str,
        operating_as: Optional[str] = None,
        painted_as: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            'flight_datetime_from': flight_datetime_from,
            'flight_datetime_to': flight_datetime_to,
            'limit': limit,
        }
        if operating_as:
            params['operating_as'] = operating_as
        if painted_as:
            params['painted_as'] = painted_as
        return self._request('GET', '/api/flight-summary/full', params=params)

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
            reason = self._error_reason(response)
            raise FlightradarError(
                self._friendly_error(response, method, path),
                status_code=response.status_code,
                reason=reason,
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

    @classmethod
    def _friendly_error(cls, response: requests.Response, method: str, path: str) -> str:
        detail = cls._safe_error(response)
        if response.status_code == 401:
            return f'Flightradar24 rejected the API key: {detail}'
        if response.status_code == 402:
            return f'Flightradar24 plan or credit limit blocked this request: {detail}'
        if response.status_code == 403:
            return f'Flightradar24 access is not allowed for this endpoint: {detail}'
        return f'Flightradar24 {method} {path} failed: {response.status_code} {detail}'

    @staticmethod
    def _error_reason(response: requests.Response) -> str:
        if response.status_code == 401:
            return 'invalid_api_key'
        if response.status_code == 402:
            return 'subscription_or_credit_required'
        if response.status_code == 403:
            return 'endpoint_forbidden'
        if response.status_code == 404:
            return 'not_found'
        if response.status_code == 429:
            return 'rate_limited'
        return 'api_error'
