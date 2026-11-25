"""Web Push (api/push.py): the message a phone decrypts, the VAPID header a push service
checks, and which reminders are due when."""

import base64
import datetime
import json
import os
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from broombuster import calfeed
from broombuster.api import push

LA = ZoneInfo("America/Los_Angeles")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def subscriber():
    """A browser's side: its key and auth secret, and the keys as PushSubscription gives them."""
    key, secret = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
    point = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return key, secret, {"p256dh": _b64(point), "auth": _b64(secret)}


def decrypt(body: bytes, key, secret: bytes) -> bytes:
    """RFC 8291 from the receiving side, with the library's HKDF."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    sender, record = body[21 : 21 + idlen], body[21 + idlen :]
    assert rs == push.RECORD_SIZE and len(record) <= rs
    ua_pub = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = key.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender)
    )
    info = b"WebPush: info\x00" + ua_pub + sender
    ikm = HKDF(hashes.SHA256(), 32, salt=secret, info=info).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, record, None)
    assert plain.endswith(b"\x02")  # the last (only) record, no padding
    return plain[:-1]


def test_message_decrypts_for_its_subscriber_only():
    key, secret, keys = subscriber()
    payload = json.dumps({"title": "Move Civic", "body": "Tomorrow 8AM–10AM"}).encode()
    body = push.encrypt(payload, keys["p256dh"], keys["auth"])
    assert decrypt(body, key, secret) == payload
    other, other_secret, _ = subscriber()
    with pytest.raises(Exception):  # noqa: B017 (AESGCM raises InvalidTag)
        decrypt(body, other, other_secret)
    with pytest.raises(ValueError, match="too long"):
        push.encrypt(b"x" * push.RECORD_SIZE, keys["p256dh"], keys["auth"])


def test_vapid_header_verifies_and_the_key_is_kept(monkeypatch, tmp_path):
    monkeypatch.setenv("BB_BASE_URL", "https://broombuster.com")
    path = tmp_path / "vapid.pem"
    monkeypatch.setenv("BB_VAPID_KEY", str(path))
    header = push._vapid("https://web.push.apple.com/QGuk", 1_000_000)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    t, k = (part.split("=", 1)[1] for part in header.removeprefix("vapid ").split(", "))
    head, claims, sig = t.split(".")
    assert json.loads(_unb64(claims)) == {
        "aud": "https://web.push.apple.com",
        "exp": 1_000_000 + 12 * 3600,
        "sub": "https://broombuster.com",
    }
    raw = _unb64(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _unb64(k))
    pub.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if wrong
    push._keys.clear()  # a restart reads the same key back
    assert push.public_key() == k


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://web.push.apple.com/QGuk", True),
        ("https://fcm.googleapis.com/fcm/send/x", True),
        ("https://wns2-by3p.notify.windows.com/w/?token=x", True),
        ("http://web.push.apple.com/QGuk", False),
        ("https://web.push.apple.com.evil.example/x", False),
        ("https://localhost/x", False),
    ],
)
def test_only_known_push_services(url, ok):
    assert push.allowed_endpoint(url) is ok


# A car on a street swept Wednesdays 8-10AM and, on its odd side, all day Thursdays.
SPOT = calfeed.CarSpot(
    "bay_area",
    "alameda",
    "1 Oak St, Alameda",
    {"even": [["W", "", "8:00 AM - 10:00 AM"]], "odd": [["TH", "", ""]]},
    ("North", "South"),
)
PREFS = {"cars": [{"id": "c1", "name": "Civic", "lat": 37.77, "lon": -122.27}]}


def _due(since, now):
    return push.due(PREFS, lambda lat, lon: SPOT, since, now)


def _at(day, hh, mm=0):
    return datetime.datetime(2026, 12, day, hh, mm, tzinfo=LA)


def test_reminders_the_evening_before_and_an_hour_before():
    # Wed Dec 2 8-10AM: 8PM Tuesday, 7AM Wednesday.
    ((key, msg, ttl),) = _due(_at(1, 19, 50), _at(1, 20, 5))
    assert msg == {
        "title": "Move Civic",
        "body": "Tomorrow 8AM–10AM · 1 Oak St, Alameda, North side",
        "tag": "sweep-c1-20261202-even-480@broombuster",
    }
    assert key == msg["tag"] + "-43200"
    assert ttl == (_at(2, 10) - _at(1, 20, 5)).total_seconds()
    ((key2, msg2, _),) = _due(_at(2, 6, 40), _at(2, 7, 1))
    assert msg2["body"].startswith("Today 8AM–10AM") and key2 != key and msg2["tag"] == msg["tag"]
    assert _due(_at(2, 7, 1), _at(2, 7, 30)) == []  # already in the window before
    # Thu Dec 3, all day on the odd side: 7PM Wednesday, 6AM Thursday.
    ((_, eve, _),) = _due(_at(2, 18, 59), _at(2, 19))
    assert eve["body"] == "Tomorrow · 1 Oak St, Alameda, South side"
    ((_, morning, _),) = _due(_at(3, 5, 59), _at(3, 6))
    assert morning["body"].startswith("Today · ")
