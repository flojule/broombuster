"""Web Push (RFC 8030) to the app on a phone: reminders of the account's cars' sweeps and
its homes' trash pickups, at the times each person picks (auth.Notify).

Each message is encrypted for the subscription (RFC 8291, aes128gcm) and signed with the
server's VAPID key (RFC 8292), a P-256 key made on first use at BB_VAPID_KEY.
"""

import base64
import datetime
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from broombuster import calfeed, config, net

from . import auth

log = logging.getLogger(__name__)

CHECK_S = 60  # how often due reminders are looked for
LOOKBACK = datetime.timedelta(minutes=30)  # a reminder missed (a restart) still goes this late
TIMEOUT_S = 10
RECORD_SIZE = 4096  # one aes128gcm record holds the whole message
# The push services of Safari, Chrome, Firefox and Edge; a subscription elsewhere is refused,
# so the server never posts to an address a client chose.
PUSH_HOSTS = (
    "web.push.apple.com",
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "notify.windows.com",
)


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


# ── Keys ──────────────────────────────────────────────────────────────────────
_keys: dict[str, ec.EllipticCurvePrivateKey] = {}  # path -> key
_keys_lock = threading.Lock()


def _private_key() -> ec.EllipticCurvePrivateKey:
    """The VAPID key at BB_VAPID_KEY, made there (mode 600) when missing."""
    path = config.vapid_key_path()
    with _keys_lock:
        if path not in _keys:
            try:
                with open(path, "rb") as f:
                    key = serialization.load_pem_private_key(f.read(), None)
            except FileNotFoundError:
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                with os.fdopen(
                    os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                ) as f:
                    f.write(pem)
                log.info("made the Web Push key %s", path)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise RuntimeError(f"{path} is not an EC private key.")
            _keys[path] = key
        return _keys[path]


def public_key() -> str:
    """The VAPID public key, as the browser's applicationServerKey (base64url)."""
    return _b64e(_point(_private_key().public_key()))


def allowed_endpoint(url: str) -> bool:
    u = urlsplit(url)
    host = u.hostname or ""
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def valid_keys(p256dh: str, auth_secret: str) -> bool:
    """A subscription's keys: a P-256 point and a 16-byte secret."""
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _b64d(p256dh))
        return len(_b64d(auth_secret)) == 16
    except ValueError:
        return False


# ── Message ───────────────────────────────────────────────────────────────────
def encrypt(
    payload: bytes,
    p256dh: str,
    auth_secret: str,
    *,
    salt: bytes | None = None,
    sender: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """The aes128gcm body of a push message (RFC 8291 section 3). salt and sender (the
    one-time key) are random unless given."""
    if len(payload) + 17 > RECORD_SIZE:
        raise ValueError("Push message too long.")
    ua_pub = _b64d(p256dh)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    as_pub = _point(sender.public_key())
    shared = sender.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    )
    ikm = _hmac(_hmac(_b64d(auth_secret), shared), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")
    salt = salt or secrets.token_bytes(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02: the last record
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + record


def _vapid(endpoint: str, now: float) -> str:
    """The Authorization header for a push service (RFC 8292): a JWT for 12 h."""
    u = urlsplit(endpoint)
    head = _b64e(b'{"typ":"JWT","alg":"ES256"}')
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(now) + 12 * 3600}
    claims["sub"] = config.base_url()
    body = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    der = _private_key().sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = _b64e(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={public_key()}"


def deliver(sub, message: dict, ttl: int) -> None:
    """Send one message to one subscription; a subscription the service no longer knows
    (404, 410) is deleted. Failures are logged, not raised."""
    endpoint = sub["endpoint"]
    host = urlsplit(endpoint).hostname
    body = encrypt(json.dumps(message).encode(), sub["p256dh"], sub["auth"])
    headers = {
        "Authorization": _vapid(endpoint, time.time()),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(max(60, ttl)),
        "Urgency": "high",
    }
    try:
        r = net.session.post(endpoint, data=body, headers=headers, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        log.warning("push to %s failed: %s", host, e)
        return
    if r.status_code in (404, 410):
        auth.delete_push_sub(endpoint)
        log.info("push subscription at %s expired: deleted", host)
    elif not r.ok:
        log.warning("push to %s refused: %d %s", host, r.status_code, r.text[:200])


# ── Reminders ─────────────────────────────────────────────────────────────────
def due(
    prefs: dict,
    locate: Callable[[float, float], calfeed.CarSpot | None],
    since: datetime.datetime,
    now: datetime.datetime,
    notify: auth.Notify,
) -> list[tuple[str, dict, int]]:
    """The reminders of an account's cars and homes due in (since, now] at the times
    `notify` picks: (key, message, ttl_s). A key is the event's uid and the reminder's
    offset from its start, the same for everyone who gets it then."""
    out = []

    def add(uid, start, at, title, what, where, end):
        if since < at <= now:
            day = "Today" if at.date() == start.date() else "Tomorrow"
            body = f"{day} {what}".rstrip() + f" · {where}"
            ttl = int((end - now).total_seconds())
            key = f"{uid}{(at - start).total_seconds():+.0f}"
            out.append((key, {"title": title, "body": body, "tag": uid}, ttl))

    def evening(start, hour):  # `hour` o'clock the day before `start`
        return start.replace(hour=hour) - datetime.timedelta(days=1)

    for sw in calfeed.sweeps(prefs, locate, now, days=1):
        midnight = sw.start.replace(hour=0, minute=0)
        times = set()
        if notify.car_evening is not None:
            times.add(evening(midnight, notify.car_evening))
        if notify.car_before is not None:
            lead = datetime.timedelta(minutes=notify.car_before)
            times.add(sw.start - lead if sw.end else sw.start.replace(hour=6))
        end = sw.end or midnight + datetime.timedelta(days=1)
        for at in sorted(times):
            add(sw.uid, sw.start, at, f"Move {sw.car_name}", sw.when, sw.where, end)
    if notify.trash_evening is not None:
        for pk in calfeed.pickups(prefs, now):
            if pk.day - now.astimezone(pk.start.tzinfo).date() <= datetime.timedelta(days=1):
                where = f"{', '.join(pk.streams)} · {pk.address or pk.label}"
                at = evening(pk.start, notify.trash_evening)
                end = pk.start + datetime.timedelta(hours=12)
                add(pk.uid, pk.start, at, "Take out the trash", "", where, end)
    return out


def run_once(locate, now: datetime.datetime) -> None:
    """Send every reminder due since LOOKBACK that was not sent yet, to each subscription
    whose person wants it then."""
    for account_id, prefs, subs in auth.push_targets():
        by_notify: dict[str | None, list] = {}
        for sub in subs:
            by_notify.setdefault(sub["notify"], []).append(sub)
        sends: dict[str, tuple[dict, int, list]] = {}
        for text, group in by_notify.items():
            for key, message, ttl in due(prefs, locate, now - LOOKBACK, now, auth.notify_of(text)):
                sends.setdefault(key, (message, ttl, []))[2].extend(group)
        for key, (message, ttl, targets) in sends.items():
            if auth.claim_push(account_id, key):
                for sub in targets:
                    deliver(sub, message, ttl)


def start(locate: Callable[[float, float], calfeed.CarSpot | None]) -> threading.Event:
    """Look for due reminders every CHECK_S in a background thread; set the returned
    event to stop it."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(CHECK_S):
            try:
                run_once(locate, datetime.datetime.now(datetime.UTC))
            except Exception:
                log.exception("push reminders failed")

    threading.Thread(target=loop, name="push", daemon=True).start()
    return stop
