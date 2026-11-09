"""Upload an exported .osm to the organisers' check server.

Contract, from the hack task (B216-lab/hack_task) and the live server at
http://innerlogistics.lovethosetrains.com:7078:

* ``POST /api/submit``
* ``Authorization: Bearer <ORGANIZER_TOKEN>`` (a missing or unknown token is
  rejected before the body is read)
* one multipart part named ``osm`` whose filename is ``{TEAM_NAME}.osm``

The earlier django-app in B216-lab/MatSim-SUMO-Webots read a part named
``file``. The server that replaced it answers
``Прикрепите файл .osm в поле «osm».`` when that old name is used.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
import uuid

ORGANIZER_URL = "http://innerlogistics.lovethosetrains.com:7078"
_TEAM_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class OrganizerError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def credentials(team_override: str | None = None) -> tuple[str, str, str]:
    """Return ``(token, team, base_url)`` or raise OrganizerError 400 if unset."""
    token = os.environ.get("ORGANIZER_TOKEN", "").strip()
    if not token:
        raise OrganizerError(
            400,
            "Не задан ORGANIZER_TOKEN — токен команды для сервера проверки. "
            "Добавьте его в окружение процесса и перезапустите сервер.",
        )
    team = (team_override or "").strip() or os.environ.get("TEAM_NAME", "").strip()
    if not team:
        raise OrganizerError(
            400,
            "Не задано имя команды: укажите TEAM_NAME в окружении сервера "
            "(файл на сервере проверки должен называться так же, как команда).",
        )
    if not _TEAM_RE.fullmatch(team):
        raise OrganizerError(400, "Имя команды может содержать только буквы, цифры, _ и -.")
    base = os.environ.get("ORGANIZER_URL", ORGANIZER_URL).strip() or ORGANIZER_URL
    return token, team, base


def _open(req: urllib.request.Request, timeout: int = 120):
    opener = urllib.request.build_opener(_NoRedirect())
    return opener.open(req, timeout=timeout)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def upload_osm(osm_bytes: bytes, token: str, team: str, base_url: str) -> dict:
    filename = f"{team}.osm"
    boundary = "----uds" + uuid.uuid4().hex
    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="osm"; filename="{filename}"\r\n'
        f"Content-Type: application/xml\r\n\r\n"
    ).encode()
    body = preamble + osm_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/submit",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with _open(req) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except OSError as exc:
        raise OrganizerError(502, f"сервер проверки недоступен: {exc}") from exc
    text = raw.decode("utf-8", errors="replace").strip()
    if status >= 400:
        detail = text or f"HTTP {status}"
        # Keep the organisers' status when it is already a client error (bad token, bad file).
        raise OrganizerError(status if 400 <= status < 500 else 502, f"сервер проверки: {detail[:1500]}")
    return {
        "ok": True,
        "status": status,
        "filename": filename,
        "message": text or "Файл принят, пайплайн MatSim → SUMO → Webots поставлен в очередь.",
    }
