"""Fail-closed, atomic daily admission for paid Muse attempts."""
from __future__ import annotations

from datetime import datetime, timezone
import os


def _client():
    # Lambda's Python runtime includes boto3. Tight timeouts keep an unavailable
    # counter from consuming the browser's entire explanation timeout.
    import boto3
    from botocore.config import Config
    return boto3.client('dynamodb', config=Config(
        connect_timeout=1, read_timeout=2, retries={'total_max_attempts': 2}))


def claim_daily_attempt(*, now: datetime | None = None, client=None) -> str:
    """Return ALLOWED, DAILY_QUOTA_EXHAUSTED, or QUOTA_UNAVAILABLE.

    Admission happens before the paid API call. A timed-out counter write is
    never treated as permission, even if DynamoDB may have committed it.
    """
    table = os.environ.get('WATTDOCKET_MUSE_QUOTA_TABLE', '').strip()
    try:
        limit = int(os.environ.get('WATTDOCKET_MUSE_DAILY_LIMIT', '100'))
    except ValueError:
        return 'QUOTA_UNAVAILABLE'
    if not table or limit < 1:
        return 'QUOTA_UNAVAILABLE'
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return 'QUOTA_UNAVAILABLE'
    now = now.astimezone(timezone.utc)
    day = now.date().isoformat()
    # TTL is storage cleanup, never the boundary used for admission.
    ttl = int(now.timestamp()) + 8 * 86400
    try:
        (client if client is not None else _client()).update_item(
            TableName=table,
            Key={'pk': {'S': 'muse#' + day}},
            UpdateExpression='SET #ttl = if_not_exists(#ttl, :ttl) ADD #n :one',
            ConditionExpression='attribute_not_exists(#n) OR #n < :limit',
            ExpressionAttributeNames={'#n': 'attempts', '#ttl': 'expires_at'},
            ExpressionAttributeValues={':one': {'N': '1'}, ':limit': {'N': str(limit)},
                                       ':ttl': {'N': str(ttl)}},
        )
        return 'ALLOWED'
    except Exception as exc:  # Treat uncertain writes and missing permissions as denial.
        response = getattr(exc, 'response', None)
        code = response.get('Error', {}).get('Code') if isinstance(response, dict) else None
        return 'DAILY_QUOTA_EXHAUSTED' if code == 'ConditionalCheckFailedException' else 'QUOTA_UNAVAILABLE'
