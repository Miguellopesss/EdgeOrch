import os
import sys
import uuid
from typing import Dict, Optional

import requests
from dotenv import load_dotenv


def build_headers(origin: str, release: str, resource_type: Optional[int] = None) -> Dict[str, str]:
    headers = {
        "X-M2M-Origin": origin,
        "X-M2M-RVI": release,
        "X-M2M-RI": str(uuid.uuid4()),
        "Accept": "application/json",
    }
    if resource_type is not None:
        headers["Content-Type"] = f"application/json;ty={resource_type}"
    return headers


def create_resource(
    session: requests.Session,
    url: str,
    payload: dict,
    origin: str,
    release: str,
    resource_type: int,
    resource_label: str,
) -> bool:
    print(f"[INFO] Creating {resource_label} at {url}")
    response = session.post(
        url,
        json=payload,
        headers=build_headers(origin, release, resource_type),
        timeout=15,
    )
    response_body = {}
    try:
        response_body = response.json()
    except ValueError:
        response_body = {}

    if response.status_code == 201:
        print(f"[OK] {resource_label} created successfully.")
        return True

    if response.status_code == 409:
        print(f"[OK] {resource_label} already exists.")
        return True

    debug_message = str(response_body.get("m2m:dbg", ""))
    if resource_type == 2 and response.status_code == 403 and "Originator has already registered" in debug_message:
        print(f"[OK] {resource_label} already exists.")
        return True

    print(f"[ERROR] Failed to create {resource_label}. HTTP {response.status_code}")
    if response_body:
        print(response_body)
    else:
        print(response.text)
    return False


def main() -> int:
    load_dotenv()

    cse_url = os.getenv("CSE_URL")
    cse_base = os.getenv("CSE_BASE")
    ae_name = os.getenv("AE_NAME")
    ae_origin = os.getenv("AE_ORIGIN")
    onem2m_release = os.getenv("ONEM2M_RELEASE")

    required_values = {
        "CSE_URL": cse_url,
        "CSE_BASE": cse_base,
        "AE_NAME": ae_name,
        "AE_ORIGIN": ae_origin,
        "ONEM2M_RELEASE": onem2m_release,
    }

    missing = [key for key, value in required_values.items() if not value]
    if missing:
        print(f"[ERROR] Missing environment variables: {', '.join(missing)}")
        return 1

    base_url = f"{cse_url.rstrip('/')}/{cse_base.strip('/')}"
    ae_url = f"{base_url}/{ae_name}"

    ae_payload = {
        "m2m:ae": {
            "rn": ae_name,
            "api": "N.org.demo.provisioning",
            "rr": True,
            "srv": [onem2m_release],
        }
    }

    container_names = ["requests", "claims", "results"]

    print("[INFO] Starting AE_Provisioning setup")
    print(f"[INFO] CSE base URL: {base_url}")
    print(f"[INFO] AE name: {ae_name}")

    session = requests.Session()

    if not create_resource(
        session=session,
        url=base_url,
        payload=ae_payload,
        origin=ae_origin,
        release=onem2m_release,
        resource_type=2,
        resource_label=f"AE {ae_name}",
    ):
        return 1

    for container_name in container_names:
        container_payload = {"m2m:cnt": {"rn": container_name}}
        if not create_resource(
            session=session,
            url=ae_url,
            payload=container_payload,
            origin=ae_origin,
            release=onem2m_release,
            resource_type=3,
            resource_label=f"container {container_name}",
        ):
            return 1

    print("[DONE] Provisioning structure is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
