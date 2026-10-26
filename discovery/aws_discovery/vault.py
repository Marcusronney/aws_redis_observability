"""TLS-only Vault KV-v2 access via Kubernetes auth; exceptions never contain bodies."""
import json
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.request


class CredentialError(RuntimeError):
    pass


def credential_path(target, mount="kv", prefix="redis-observability"):
    parts = [mount, "data", prefix, str(target["account_id"]), target["region"],
             target.get("aws_resource_type", "replication_group"), target["replication_group_id"]]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
        raise CredentialError("Invalid resource identity for Vault path")
    return "/".join(parts)


class Vault:
    def __init__(self, address, ca_file, role, jwt_file, mount="kv", prefix="redis-observability"):
        if not address.startswith("https://"):
            raise CredentialError("Vault requires HTTPS")
        self.address, self.mount, self.prefix = address.rstrip("/"), mount, prefix
        self.context = ssl.create_default_context(cafile=ca_file)
        self.token = None
        result = self.request("POST", "auth/kubernetes/login", {
            "role": role, "jwt": Path(jwt_file).read_text().strip()})
        self.token = result["auth"]["client_token"]

    def request(self, method, path, body=None):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Vault-Token"] = self.token
        request = urllib.request.Request(self.address + "/v1/" + path, method=method,
                    headers=headers, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise CredentialError(f"Vault operation failed (HTTP {exc.code})") from None
        except (OSError, ValueError):
            raise CredentialError("Vault transport or response failed") from None

    def read(self, target):
        try:
            values = self.request("GET", credential_path(target, self.mount, self.prefix))["data"]["data"]
            password, username = values["password"], values.get("username", "default")
            if not isinstance(password, str) or not password or not isinstance(username, str):
                raise ValueError()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", username):
                raise ValueError()
            return username, password
        except (KeyError, TypeError, ValueError):
            raise CredentialError("Vault credential is missing or invalid") from None

    def close(self):
        try:
            self.request("POST", "auth/token/revoke-self", {})
        finally:
            self.token = None
