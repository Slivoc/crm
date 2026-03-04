import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


class PartsBaseError(RuntimeError):
    """Raised when PartsBase returns an error response."""


@dataclass
class PartsBaseConfig:
    auth_url: str
    api_base_url: str
    client_id: str
    client_secret: str
    username: str
    password: str
    scope: str = 'api openid'
    grant_type: str = 'password'
    timeout: int = 60


class PartsBaseClient:
    def __init__(self, config: PartsBaseConfig, *, session: Optional[requests.Session] = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def get_access_token(self) -> str:
        payload = {
            'grant_type': self.config.grant_type,
            'client_id': self.config.client_id,
            'client_secret': self.config.client_secret,
            'scope': self.config.scope,
            'username': self.config.username,
            'password': self.config.password,
        }
        response = self.session.post(self.config.auth_url, data=payload, timeout=self.config.timeout)
        if response.status_code >= 400:
            raise PartsBaseError(
                f'PartsBase token request failed: {response.status_code} {self._safe_error(response)}'
            )
        body = response.json()
        token = body.get('access_token')
        if not token:
            raise PartsBaseError('PartsBase token request succeeded but access_token was missing.')
        return token

    def submit_inventory_availability_zip(self, zip_bytes: bytes, *, filename: str = 'parts.zip') -> Dict[str, Any]:
        token = self.get_access_token()
        files = {'file': (filename, zip_bytes, 'application/zip')}
        return self._request('POST', '/api/inventoryAvailabilities', token=token, files=files)

    def get_inventory_availability_status(self, request_id: str) -> Dict[str, Any]:
        token = self.get_access_token()
        return self._request('GET', f'/api/inventoryAvailabilities/{request_id}/status', token=token)

    def get_inventory_availability_result(self, request_id: str) -> Tuple[bytes, str]:
        token = self.get_access_token()
        content, content_type = self._request(
            'GET',
            f'/api/inventoryAvailabilities/{request_id}',
            token=token,
            expect_json=False,
        )
        return content, content_type

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        files: Optional[Dict[str, Any]] = None,
        expect_json: bool = True,
    ) -> Any:
        headers = {'Authorization': f'Bearer {token}'}
        response = self.session.request(
            method,
            f"{self.config.api_base_url.rstrip('/')}/{path.lstrip('/')}",
            headers=headers,
            files=files,
            timeout=self.config.timeout,
        )
        if response.status_code >= 400:
            raise PartsBaseError(
                f'PartsBase {method} {path} failed: {response.status_code} {self._safe_error(response)}'
            )
        if expect_json:
            if not response.content:
                return {}
            return response.json()
        return response.content, response.headers.get('Content-Type', 'application/octet-stream')

    @staticmethod
    def create_test_search_zip(parts: List[str]) -> bytes:
        normalized = [part.strip() for part in parts if part and part.strip()]
        if not normalized:
            raise ValueError('At least one part number is required.')

        lines = ['PartNumber'] + normalized
        csv_payload = '\n'.join(lines).encode('utf-8')

        # PartsBase expects a schema descriptor alongside the CSV file inside
        # the uploaded ZIP. Without this file, the API rejects the upload with
        # `Schema file wasn't found`.
        schema_payload = '\n'.join(
            [
                '[parts.csv]',
                'ColNameHeader=True',
                'Format=CSVDelimited',
                'MaxScanRows=0',
                'CharacterSet=65001',
            ]
        ).encode('utf-8')

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr('parts.csv', csv_payload)
            zip_file.writestr('schema.ini', schema_payload)
        return buffer.getvalue()

    @staticmethod
    def _safe_error(response: requests.Response) -> str:
        try:
            return json.dumps(response.json())
        except Exception:
            return response.text[:1000]
