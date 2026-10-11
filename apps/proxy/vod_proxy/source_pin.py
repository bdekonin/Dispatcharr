"""Keep a playback session on one upstream byte layout, across workers."""

import json

PIN_TTL = 86400


def read_source_pin(redis_client, session_id, content_type, content_id):
    key = f"vod_source_pin:{session_id}"
    raw = redis_client.get(key)
    if raw is None:
        state = redis_client.hgetall(f"vod_persistent_connection:{session_id}")
        if not state:
            return None
        pin = {
            "content_type": state.get("content_obj_type"),
            "content_id": state.get("content_uuid"),
            "profile_id": state.get("m3u_profile_id"),
            "stream_url": state.get("stream_url"),
        }
        return claim_source_pin(redis_client, session_id, content_type, content_id, pin)
    return _validate_pin(redis_client, key, raw, content_type, content_id)


def _validate_pin(redis_client, key, raw, content_type, content_id):
    pin = json.loads(raw)
    if (
        pin.get("content_type") != content_type
        or pin.get("content_id") != str(content_id)
        or not pin.get("profile_id")
        or not isinstance(pin.get("stream_url"), str)
        or not pin["stream_url"].startswith(("http://", "https://"))
    ):
        raise ValueError("Invalid source pin or session reused for different content")
    if not redis_client.expire(key, PIN_TTL):
        raise ValueError("Source pin expired during selection")
    return pin


def claim_source_pin(redis_client, session_id, content_type, content_id, pin):
    redis_client.set(
        f"vod_source_pin:{session_id}", json.dumps(pin), nx=True, ex=PIN_TTL
    )
    key = f"vod_source_pin:{session_id}"
    raw = redis_client.get(key)
    if raw is None:
        raise ValueError("Source pin disappeared during selection")
    return _validate_pin(redis_client, key, raw, content_type, content_id)
