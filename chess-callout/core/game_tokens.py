"""
Per-player signed game tokens.

Each accepted callout produces TWO links, not one shared link — one for
white, one for black. Each token cryptographically encodes (game_id,
user_id, color), signed with a server-side secret. Knowing your own
link proves you are that specific player; it can't be edited to claim
the other color, and it can't be forged without GAME_TOKEN_SECRET.

This is deliberately stateless (no DB lookup needed to verify a token) —
the move API just decodes the token and checks it against the game row
it already has to load.
"""
import os
from itsdangerous import URLSafeSerializer, BadSignature

_SALT = "chess-game-token"


def _serializer() -> URLSafeSerializer:
    # read lazily so importing this module doesn't require the secret
    # to be set in processes that don't generate/verify tokens
    secret = os.environ["GAME_TOKEN_SECRET"]
    return URLSafeSerializer(secret, salt=_SALT)


def generate_player_token(game_id: str, user_id: str, color: str) -> str:
    assert color in ("white", "black")
    return _serializer().dumps({"game_id": game_id, "user_id": user_id, "color": color})


def verify_player_token(token: str) -> dict | None:
    """Returns {'game_id', 'user_id', 'color'} or None if invalid/tampered."""
    try:
        payload = _serializer().loads(token)
    except BadSignature:
        return None
    if not all(k in payload for k in ("game_id", "user_id", "color")):
        return None
    return payload
