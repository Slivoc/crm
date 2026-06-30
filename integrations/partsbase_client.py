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

    def submit_inventory_import_zip(self, zip_bytes: bytes, *, filename: str = 'parts.zip') -> Dict[str, Any]:
        token = self.get_access_token()
        files = {'file': (filename, zip_bytes, 'application/zip')}
        return self._request('POST', '/api/inventoryImportStocks', token=token, files=files)

    def get_inventory_availability_status(self, request_id: str) -> Dict[str, Any]:
        token = self.get_access_token()
        return self._request('GET', f'/api/inventoryAvailabilities/{request_id}/status', token=token)

    def get_inventory_import_status(self, request_id: str) -> Dict[str, Any]:
        token = self.get_access_token()
        return self._request('GET', f'/api/inventoryImportStocks/{request_id}/status', token=token)

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
    def create_inventory_upload_zip(rows: List[Dict[str, Any]]) -> bytes:
        normalized_rows = []
        for raw in rows or []:
            part_number = str(raw.get('part_number') or '').strip().replace(' ', '')
            if not part_number:
                continue
            normalized_rows.append([
                str(raw.get('action_code') or 'A').strip()[:1] or 'A',
                part_number,
                str(raw.get('description') or '').strip(),
                str(raw.get('alternate_part_number') or '').strip().replace(' ', ''),
                str(raw.get('condition_code') or '').strip(),
                str(raw.get('quantity') if raw.get('quantity') is not None else ''),
                str(raw.get('uom') or '').strip(),
                str(raw.get('manufacturer') or '').strip(),
                str(raw.get('unit_price') if raw.get('unit_price') is not None else ''),
                str(raw.get('aircraft_type') or '').strip(),
                str(raw.get('engine_type') or '').strip(),
                str(raw.get('serial_number') or '').strip(),
                str(raw.get('traceability') or '').strip(),
                str(raw.get('trace_to') or '').strip(),
                str(raw.get('image_url') or '').strip(),
                str(raw.get('documentation_url') or '').strip(),
                str(raw.get('documentation_caption') or '').strip(),
            ])

        if not normalized_rows:
            raise ValueError('At least one valid inventory row is required.')

        data_payload = '\r\n'.join('\t'.join(row) for row in normalized_rows).encode('utf-8')
        manifest_payload = PartsBaseClient._build_manifest_payload()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr('Data.dat', data_payload)
            zip_file.writestr('Manifest.xml', manifest_payload)
        return buffer.getvalue()

    @staticmethod
    def create_test_search_zip(parts: List[str]) -> bytes:
        normalized = [part.strip() for part in parts if part and part.strip()]
        if not normalized:
            raise ValueError('At least one part number is required.')

        data_rows = []
        for index, part in enumerate(normalized, start=1):
            data_rows.append(''.join([
                PartsBaseClient._fixed_width(index, 10),
                PartsBaseClient._fixed_width(part.replace(' ', ''), 50),
                PartsBaseClient._fixed_width('', 80),
                PartsBaseClient._fixed_width('', 5),
            ]))

        manifest_payload = '\n'.join(
            [
                '<?xml version="1.0" encoding="unicode"?>',
                '<FIELDS>',
                '  <FIELD NAME="LINE_INDEX" LENGTH="10" TYPE="DECIMAL" />',
                '  <FIELD NAME="PARTNUMBER" LENGTH="50" TYPE="CHAR" />',
                '  <FIELD NAME="DESCRIPTION" LENGTH="80" TYPE="CHAR" />',
                '  <FIELD NAME="CONDITION" LENGTH="5" TYPE="CHAR" />',
                '</FIELDS>',
            ]
        ).encode('utf-16')
        data_payload = '\r\n'.join(data_rows).encode('utf-8')

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr('Data.dat', data_payload)
            zip_file.writestr('Manifest.xml', manifest_payload)
        return buffer.getvalue()

    @staticmethod
    def _build_manifest_payload() -> bytes:
        return '\n'.join(
            [
                '<?xml version="1.0" encoding="unicode"?>',
                '<FIELDS>',
                '  <FIELD NAME="ACTION_CODE" TYPE="CHAR" LENGTH="1" />',
                '  <FIELD NAME="PARTNUMBER" TYPE="CHAR" />',
                '  <FIELD NAME="DESCRIPTION" TYPE="CHAR" />',
                '  <FIELD NAME="ALTERNATEPARTNUMBER" TYPE="CHAR" />',
                '  <FIELD NAME="CONDITIONCODE" TYPE="CHAR" />',
                '  <FIELD NAME="QUANTITY" TYPE="INTEGER" />',
                '  <FIELD NAME="UOM" TYPE="CHAR" />',
                '  <FIELD NAME="MANUFACTURER" TYPE="CHAR" />',
                '  <FIELD NAME="UNITPRICE" TYPE="DECIMAL" />',
                '  <FIELD NAME="AIRCRAFTTYPE" TYPE="CHAR" />',
                '  <FIELD NAME="ENGINETYPE" TYPE="CHAR" />',
                '  <FIELD NAME="SERIALNUMBER" TYPE="CHAR" />',
                '  <FIELD NAME="TRACEABILITY" TYPE="CHAR" />',
                '  <FIELD NAME="TRACETO" TYPE="CHAR" />',
                '  <FIELD NAME="IMAGEURL" TYPE="CHAR" />',
                '  <FIELD NAME="DOCUMENTATIONURL" TYPE="CHAR" />',
                '  <FIELD NAME="DOCUMENTATIONCAPTION" TYPE="CHAR" />',
                '</FIELDS>',
            ]
        ).encode('utf-16')

    @staticmethod
    def _fixed_width(value: Any, length: int) -> str:
        return str(value or '')[:length].ljust(length)

    @staticmethod
    def _safe_error(response: requests.Response) -> str:
        try:
            return json.dumps(response.json())
        except Exception:
            return response.text[:1000]
