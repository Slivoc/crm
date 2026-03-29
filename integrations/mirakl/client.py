import json
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin

import requests


class MiraklError(RuntimeError):
    """Raised when Mirakl returns an error response."""


class MiraklClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: int = 30,
        shop_id: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not base_url:
            raise ValueError("Mirakl base_url is required.")
        if not api_key:
            raise ValueError("Mirakl api_key is required.")
        self.base_url = base_url.rstrip('/') + '/'
        self.api_key = api_key
        self.timeout = timeout
        self.shop_id = shop_id
        self.session = session or requests.Session()

    def _headers(self) -> Dict[str, str]:
        headers = {
            'Authorization': self.api_key,
            'Accept': 'application/json',
        }
        if self.shop_id:
            headers['X-Mirakl-Shop-Id'] = str(self.shop_id)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        expect_json: bool = True,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip('/'))
        headers = self._headers()
        if files:
            headers.pop('Content-Type', None)
        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_payload,
            data=data,
            files=files,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            details = response.text
            try:
                details = json.dumps(response.json())
            except Exception:
                pass
            raise MiraklError(f"Mirakl {method} {path} failed: {response.status_code} {details}")
        if expect_json:
            if not response.content:
                return {}
            return response.json()
        return response.content, response.headers.get('Content-Type', 'application/octet-stream')

    def get_account(self) -> Dict[str, Any]:
        return self.request('GET', '/api/account')

    def get_products_by_references(
        self,
        product_references: list[str],
        *,
        shop_id: Optional[str] = None,
        locale: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            'product_references': ','.join(product_references),
        }
        if shop_id:
            params['shop_id'] = shop_id
        if locale:
            params['locale'] = locale
        return self.request('GET', '/api/products', params=params)

    def import_offers(self, csv_bytes: bytes, *, import_mode: str = 'NORMAL') -> Dict[str, Any]:
        files = {'file': ('offers.csv', csv_bytes, 'text/csv')}
        data = {'import_mode': import_mode}
        return self.request('POST', '/api/offers/imports', data=data, files=files)

    def import_products(self, csv_bytes: bytes, *, import_mode: str = 'NORMAL') -> Dict[str, Any]:
        files = {'file': ('products.csv', csv_bytes, 'text/csv')}
        data = {'import_mode': import_mode}
        return self.request('POST', '/api/products/imports', data=data, files=files)

    def get_offers_import(self, import_id: str) -> Dict[str, Any]:
        return self.request('GET', f'/api/offers/imports/{import_id}')

    def get_offers_import_errors(self, import_id: str) -> Tuple[bytes, str]:
        return self.request('GET', f'/api/offers/imports/{import_id}/errors', expect_json=False)
