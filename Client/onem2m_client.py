"""Small helper for interacting with an ACME oneM2M CSE over HTTP."""

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import Config


PROVISIONING_RESULT_WRITERS = [
    "Cae-proxmox-monitor",
    "Cae-proxmox-monitor-mm01",
    "Cae-provisioning-admin",
]


class OneM2MClient:
    """Publishes provisioning requests and reads the latest result."""

    def __init__(self, config: Config, timeout: int = 15) -> None:
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()

    def _build_url(self, path: str) -> str:
        return f"{self.config.cse_url}{path}"

    def _request_headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "X-M2M-Origin": self.config.client_origin,
            "X-M2M-RVI": self.config.onem2m_release,
            "X-M2M-RI": str(uuid.uuid4()),
            "Accept": "application/json",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _get(self, path: str) -> requests.Response:
        return self.session.get(
            self._build_url(path),
            headers=self._request_headers(),
            timeout=self.timeout,
        )

    def _post(self, path: str, resource_type: int, body: Dict[str, Any]) -> requests.Response:
        return self.session.post(
            self._build_url(path),
            headers=self._request_headers(f"application/json;ty={resource_type}"),
            json=body,
            timeout=self.timeout,
        )

    def _put(self, path: str, body: Dict[str, Any]) -> requests.Response:
        return self.session.put(
            self._build_url(path),
            headers=self._request_headers("application/json"),
            json=body,
            timeout=self.timeout,
        )

    def _delete(self, path: str) -> requests.Response:
        return self.session.delete(
            self._build_url(path),
            headers=self._request_headers(),
            timeout=self.timeout,
        )

    def ensure_private_result_channel(self) -> Tuple[bool, str]:
        success, message = self._ensure_client_ae()
        if not success:
            return success, message

        success, message, acp_ri = self._ensure_client_results_acp()
        if not success or not acp_ri:
            return False, message

        success, message = self._ensure_client_results_container(acp_ri)
        if not success:
            return success, message

        return True, f"Canal privado pronto em {self.config.client_results_path}."

    def _ensure_client_ae(self) -> Tuple[bool, str]:
        try:
            response = self._post(
                f"/{self.config.cse_base}",
                2,
                {
                    "m2m:ae": {
                        "rn": self.config.client_ae_name,
                        "api": self.config.client_api,
                        "rr": False,
                        "srv": [self.config.onem2m_release],
                    }
                },
            )
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE ao criar AE do client: {exc}"

        if response.status_code in (200, 201, 409):
            return True, f"AE do client pronta: {self.config.client_ae_name}"

        payload = self._safe_json(response)
        debug_message = str(payload.get("m2m:dbg", ""))
        if response.status_code == 403 and "Originator has already registered" in debug_message:
            return True, f"AE do client pronta: {self.config.client_ae_name}"

        details = self._extract_error_details(response)
        return False, f"Nao foi possivel garantir a AE do client: {details}"

    def _ensure_client_results_acp(self) -> Tuple[bool, str, Optional[str]]:
        desired_create_body = self._build_client_results_acp_body(include_resource_name=True)
        desired_update_body = self._build_client_results_acp_body(include_resource_name=False)

        try:
            response = self._get(self.config.client_results_acp_path)
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao consultar ACP privado: {exc}", None

        if response.status_code == 200:
            payload = self._safe_json(response)
            acp = payload.get("m2m:acp", {})
            acp_ri = acp.get("ri")
            if not isinstance(acp_ri, str) or not acp_ri:
                return False, "ACP privado existe mas nao foi possivel obter o ri.", None

            try:
                update_response = self._put(self.config.client_results_acp_path, desired_update_body)
            except requests.RequestException as exc:
                return False, f"Erro de ligacao ao atualizar ACP privado: {exc}", None

            if update_response.status_code not in (200, 201):
                details = self._extract_error_details(update_response)
                return False, f"Nao foi possivel atualizar o ACP privado: {details}", None

            return True, "ACP privado atualizado com sucesso.", acp_ri

        if response.status_code != 404:
            details = self._extract_error_details(response)
            return False, f"Nao foi possivel consultar o ACP privado: {details}", None

        try:
            response = self._post(self.config.client_ae_path, 1, desired_create_body)
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao criar ACP privado: {exc}", None

        if response.status_code not in (200, 201):
            details = self._extract_error_details(response)
            return False, f"Nao foi possivel criar o ACP privado: {details}", None

        payload = self._safe_json(response)
        acp = payload.get("m2m:acp", {})
        acp_ri = acp.get("ri")
        if not isinstance(acp_ri, str) or not acp_ri:
            return False, "ACP privado criado sem ri valido.", None

        return True, "ACP privado criado com sucesso.", acp_ri

    def _build_client_results_acp_body(self, include_resource_name: bool) -> Dict[str, Any]:
        acp_body: Dict[str, Any] = {
            "pv": {
                "acr": [
                    {"acor": [self.config.client_origin], "acop": 34},
                    {
                        "acor": [self.config.client_origin],
                        "acod": [{"chty": [23]}],
                        "acop": 1,
                    },
                    {
                        "acor": PROVISIONING_RESULT_WRITERS,
                        "acod": [{"chty": [4]}],
                        "acop": 35,
                    },
                ]
            },
            "pvs": {
                "acr": [
                    {"acor": [self.config.client_origin], "acop": 63},
                ]
            },
        }
        if include_resource_name:
            acp_body["rn"] = "acp_results"
        return {"m2m:acp": acp_body}

    def _ensure_client_results_container(self, acp_ri: str) -> Tuple[bool, str]:
        try:
            response = self._get(self.config.client_results_path)
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao consultar container privado de resultados: {exc}"

        if response.status_code == 200:
            return True, "Container privado de resultados ja existe."

        if response.status_code != 404:
            details = self._extract_error_details(response)
            return False, f"Nao foi possivel consultar o container privado de resultados: {details}"

        body = {
            "m2m:cnt": {
                "rn": "results",
                "acpi": [acp_ri],
            }
        }

        try:
            response = self._post(self.config.client_ae_path, 3, body)
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao criar container privado de resultados: {exc}"

        if response.status_code not in (200, 201):
            details = self._extract_error_details(response)
            return False, f"Nao foi possivel criar o container privado de resultados: {details}"

        return True, "Container privado de resultados criado com sucesso."

    def publish_request(self, request_payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Publish a provisioning request as a contentInstance under requests.

        The 'con' field must contain the request serialized as a JSON string.
        """

        url = self._build_url(self.config.requests_path)
        body = {
            "m2m:cin": {
                "con": json.dumps(request_payload, ensure_ascii=False)
            }
        }

        try:
            response = self.session.post(
                url,
                headers=self._request_headers("application/json;ty=4"),
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao publicar pedido: {details}", None
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", None

        return True, "Pedido publicado com sucesso.", self._safe_json(response)

    def create_results_subscription(
        self,
        notification_url: str,
        resource_name: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        subscription_name = resource_name or f"sub_{uuid.uuid4().hex[:10]}"
        body = {
            "m2m:sub": {
                "rn": subscription_name,
                "enc": {
                    "net": [3],
                },
                "nu": [notification_url],
                "nct": 1,
            }
        }

        try:
            response = self._post(self.config.client_results_path, 23, body)
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao criar subscription: {exc}", None

        if response.status_code not in (200, 201):
            details = self._extract_error_details(response)
            return False, f"Nao foi possivel criar a subscription: {details}", None

        subscription_path = f"{self.config.client_results_path}/{subscription_name}"
        return True, f"Subscription criada com sucesso em {subscription_path}.", subscription_path

    def delete_resource(self, path: str) -> Tuple[bool, str]:
        try:
            response = self._delete(path)
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao remover recurso {path}: {exc}"

        if response.status_code in (200, 202, 204, 404):
            return True, f"Recurso removido: {path}"

        details = self._extract_error_details(response)
        return False, f"Nao foi possivel remover o recurso {path}: {details}"

    def retrieve_resource(self, path: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        try:
            response = self._get(path)
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao consultar recurso {path}: {exc}", None

        if response.status_code != 200:
            details = self._extract_error_details(response)
            return False, f"Nao foi possivel consultar recurso {path}: {details}", None

        return True, f"Recurso {path} obtido com sucesso.", self._safe_json(response)

    def get_latest_content(self, container_path: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        success, message, payload = self.retrieve_resource(f"{container_path}/la")
        if not success or not payload:
            return success, message, None

        parsed = self._parse_content_instance(payload)
        cin = parsed.get("m2m:cin")
        if not isinstance(cin, dict):
            return False, f"Recurso {container_path}/la nao devolveu um contentInstance valido.", None

        return True, f"Ultimo contentInstance obtido de {container_path}.", cin

    def get_latest_result(self) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Fetch the latest contentInstance from the results container."""

        url = self._build_url(self.config.latest_result_path)

        try:
            response = self.session.get(
                url,
                headers=self._request_headers(),
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return False, "Ainda nao existe nenhum resultado publicado.", None
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao consultar resultado: {details}", None
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", None

        raw_data = self._safe_json(response)
        parsed_data = self._parse_content_instance(raw_data)
        return True, "Ultimo resultado obtido com sucesso.", parsed_data

    def list_private_results(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Fetch every contentInstance under the client's private results container."""

        try:
            response = self.session.get(
                self._build_url(self.config.client_results_path) + "?rcn=4",
                headers=self._request_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao listar resultados privados: {details}", []
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", []

        raw_data = self._safe_json(response)
        parsed_results = [
            self._parse_embedded_content_instance(content_instance)
            for content_instance in self._extract_embedded_content_instances(raw_data)
        ]
        parsed_results.sort(key=lambda item: (str(item.get("ct", "")), str(item.get("ri", ""))))
        return True, "Resultados privados obtidos com sucesso.", parsed_results

    def list_provisioning_requests(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Fetch published provisioning requests visible to this client."""

        try:
            response = self.session.get(
                self._build_url(self.config.requests_path) + "?rcn=4",
                headers=self._request_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao listar pedidos de provisioning: {details}", []
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", []

        raw_data = self._safe_json(response)
        parsed_requests = [
            self._parse_embedded_content_instance(content_instance)
            for content_instance in self._extract_embedded_content_instances(raw_data)
        ]
        parsed_requests.sort(key=lambda item: (str(item.get("ct", "")), str(item.get("ri", ""))))
        return True, "Pedidos de provisioning obtidos com sucesso.", parsed_requests

    def list_rebalance_decisions(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Fetch shared rebalance decisions visible to all clients."""

        try:
            response = self.session.get(
                self._build_url(self.config.rebalance_decisions_path) + "?rcn=4",
                headers=self._request_headers(),
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return True, "Ainda nao existem decisoes de rebalanceamento.", []
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao listar decisoes de rebalanceamento: {details}", []
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", []

        raw_data = self._safe_json(response)
        parsed_decisions = [
            self._parse_embedded_content_instance(content_instance)
            for content_instance in self._extract_embedded_content_instances(raw_data)
        ]
        parsed_decisions.sort(key=lambda item: (str(item.get("ct", "")), str(item.get("ri", ""))))
        return True, "Decisoes de rebalanceamento obtidas com sucesso.", parsed_decisions

    def publish_rebalance_decision(self, decision_payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        body = {
            "m2m:cin": {
                "con": json.dumps(decision_payload, ensure_ascii=False)
            }
        }

        try:
            response = self.session.post(
                self._build_url(self.config.rebalance_decisions_path),
                headers=self._request_headers("application/json;ty=4"),
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao publicar decisao de rebalanceamento: {details}", None
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", None

        return True, "Decisao de rebalanceamento publicada com sucesso.", self._safe_json(response)

    def list_placement_reservations(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Fetch shared placement reservations visible to all clients."""

        try:
            response = self.session.get(
                self._build_url(self.config.placement_reservations_path) + "?rcn=4",
                headers=self._request_headers(),
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return True, "Ainda nao existem reservas de colocacao.", []
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao listar reservas de colocacao: {details}", []
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", []

        raw_data = self._safe_json(response)
        parsed_reservations = [
            self._parse_embedded_content_instance(content_instance)
            for content_instance in self._extract_embedded_content_instances(raw_data)
        ]
        parsed_reservations.sort(key=lambda item: (str(item.get("ct", "")), str(item.get("ri", ""))))
        return True, "Reservas de colocacao obtidas com sucesso.", parsed_reservations

    def publish_placement_reservation(self, reservation_payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        body = {
            "m2m:cin": {
                "con": json.dumps(reservation_payload, ensure_ascii=False)
            }
        }

        try:
            response = self.session.post(
                self._build_url(self.config.placement_reservations_path),
                headers=self._request_headers("application/json;ty=4"),
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            details = self._extract_error_details(exc.response)
            return False, f"Erro HTTP ao publicar reserva de colocacao: {details}", None
        except requests.RequestException as exc:
            return False, f"Erro de ligacao ao CSE: {exc}", None

        return True, "Reserva de colocacao publicada com sucesso.", self._safe_json(response)

    def get_result_for_request(self, request_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Fetch the published result that matches a specific request_id."""

        success, message, parsed_results = self.list_private_results()
        if not success:
            return False, message.replace("listar resultados privados", "consultar resultado do pedido"), None

        for parsed_content_instance in parsed_results:
            content = parsed_content_instance.get("con")
            if isinstance(content, dict) and str(content.get("request_id", "")).strip() == request_id:
                return True, f"Resultado do pedido {request_id} obtido com sucesso.", {"m2m:cin": parsed_content_instance}

        return False, f"Ainda nao existe resultado para o pedido {request_id}.", None

    def wait_for_result(
        self,
        request_id: str,
        timeout_seconds: Optional[int] = 180,
        poll_interval_seconds: int = 5,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Poll the results container until the specific request result is published."""

        deadline = None if timeout_seconds is None else time.time() + timeout_seconds

        while deadline is None or time.time() < deadline:
            success, message, result = self.get_result_for_request(request_id)
            if success:
                return success, message, result
            time.sleep(poll_interval_seconds)

        return False, f"O pedido {request_id} foi publicado, mas o resultado ainda nao apareceu dentro do timeout.", None

    @staticmethod
    def _safe_json(response: requests.Response) -> Dict[str, Any]:
        try:
            return response.json()
        except ValueError:
            return {"raw_response": response.text}

    @staticmethod
    def _extract_error_details(response: Optional[requests.Response]) -> str:
        if response is None:
            return "resposta HTTP sem detalhes."

        payload = OneM2MClient._safe_json(response)
        error_body = json.dumps(payload, ensure_ascii=False)
        return f"status {response.status_code} - {error_body}"

    @staticmethod
    def _extract_embedded_content_instances(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        container = data.get("m2m:cnt")
        if not isinstance(container, dict):
            return []

        content_instances = container.get("m2m:cin", [])
        if isinstance(content_instances, dict):
            return [content_instances]
        if isinstance(content_instances, list):
            return [item for item in content_instances if isinstance(item, dict)]
        return []

    @staticmethod
    def _parse_embedded_content_instance(content_instance: Dict[str, Any]) -> Dict[str, Any]:
        parsed_content_instance = dict(content_instance)
        content = parsed_content_instance.get("con")
        if not isinstance(content, str):
            return parsed_content_instance

        try:
            parsed_content_instance["con"] = json.loads(content)
        except json.JSONDecodeError:
            return parsed_content_instance

        return parsed_content_instance

    @staticmethod
    def _parse_content_instance(data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse the 'con' value if the CSE returned a contentInstance payload."""

        cin = data.get("m2m:cin")
        if not isinstance(cin, dict):
            return data

        content = cin.get("con")
        if not isinstance(content, str):
            return data

        try:
            parsed_content = json.loads(content)
        except json.JSONDecodeError:
            return data

        result = dict(data)
        result["m2m:cin"] = dict(cin)
        result["m2m:cin"]["con"] = parsed_content
        return result
