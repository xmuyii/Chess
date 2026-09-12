"""
Updates the official WhatsApp Cloud API business profile picture.
Real, documented Meta Graph API flow — not GOWA, not a workaround.

Three sequential calls:
  1. POST /{app_id}/uploads?file_length=...&file_type=...  -> upload session id
  2. POST /{session_id} with the raw file bytes + file_offset header -> file handle
  3. POST /{phone_number_id}/whatsapp_business_profile with profile_picture_handle

Needs WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID (already used elsewhere in
this repo) plus META_APP_ID (the same app ID visible in your Meta
developer console URL, e.g. 1625953319127009).
"""
import mimetypes
import os

import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def _initiate_upload_session(file_path: str, access_token: str, app_id: str) -> str:
    file_length = os.path.getsize(file_path)
    file_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"

    resp = requests.post(
        f"{GRAPH_BASE}/{app_id}/uploads",
        params={"file_length": file_length, "file_type": file_type, "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]  # e.g. "upload:MTphd..."


def _upload_file_bytes(session_id: str, file_path: str, access_token: str) -> str:
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    resp = requests.post(
        f"https://graph.facebook.com/{session_id}",
        headers={
            "Authorization": f"OAuth {access_token}",
            "file_offset": "0",
        },
        data=file_bytes,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["h"]  # the file handle


def _set_business_profile_picture(handle: str, access_token: str, phone_number_id: str) -> None:
    resp = requests.post(
        f"{GRAPH_BASE}/{phone_number_id}/whatsapp_business_profile",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"messaging_product": "whatsapp", "profile_picture_handle": handle},
        timeout=30,
    )
    resp.raise_for_status()


def update_profile_picture_from_file(file_path: str) -> None:
    """The one function callers need. Runs all three steps in order."""
    access_token = os.environ["WA_ACCESS_TOKEN"]
    phone_number_id = os.environ["WA_PHONE_NUMBER_ID"]
    app_id = os.environ["META_APP_ID"]

    session_id = _initiate_upload_session(file_path, access_token, app_id)
    handle = _upload_file_bytes(session_id, file_path, access_token)
    _set_business_profile_picture(handle, access_token, phone_number_id)
