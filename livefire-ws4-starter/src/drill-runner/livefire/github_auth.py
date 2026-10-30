"""The runner's GitHub credential: a GitHub App installation token (preferred) or a fine-grained token.

Permissions, on the one repository only: Actions read, Contents read, Pull requests write, Metadata read.
"""
import os
import threading
import time
from datetime import datetime

import httpx
import jwt


class AppTokens:
    """Short-lived installation tokens, narrowed to one repository and the four permissions above."""

    PERMISSIONS = {"actions": "read", "contents": "read", "pull_requests": "write", "metadata": "read"}

    def __init__(self, app_id: str, installation_id: str, private_key: str, repo_name: str, transport=None):
        self.app_id, self.installation_id, self.key = app_id, installation_id, private_key
        self.body = {"repositories": [repo_name], "permissions": self.PERMISSIONS}
        self.http = httpx.Client(base_url="https://api.github.com", timeout=20, transport=transport)
        self._token, self._expires, self._lock = None, 0.0, threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires - 300:
                return self._token
            now = int(time.time())
            assertion = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self.key, algorithm="RS256")
            r = self.http.post(f"/app/installations/{self.installation_id}/access_tokens", json=self.body,
                               headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {assertion}"})
            r.raise_for_status()
            data = r.json()
            self._token = data["token"]
            self._expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
            return self._token


def token_provider_from_env():
    if os.environ.get("GITHUB_APP_ID"):
        repo_name = os.environ["GITHUB_REPO"].split("/", 1)[1]
        return AppTokens(os.environ["GITHUB_APP_ID"], os.environ["GITHUB_INSTALLATION_ID"],
                         os.environ["GITHUB_PRIVATE_KEY"], repo_name)
    token = os.environ["GITHUB_TOKEN"]
    return lambda: token
