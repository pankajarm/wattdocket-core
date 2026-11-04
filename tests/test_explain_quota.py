"""Admission counter failure modes and the UTC-day boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'api' / 'lambda_explain'))
import quota  # noqa: E402


class ConditionalFailure(Exception):
    response = {'Error': {'Code': 'ConditionalCheckFailedException'}}


class FakeAtomicCounter:
    def __init__(self):
        self.calls = {}
        self.lock = threading.Lock()

    def update_item(self, **args):
        assert args['ConditionExpression'] == 'attribute_not_exists(#n) OR #n < :limit'
        assert args['UpdateExpression'] == 'SET #ttl = if_not_exists(#ttl, :ttl) ADD #n :one'
        assert args['ExpressionAttributeValues'][':one'] == {'N': '1'}
        key = args['Key']['pk']['S']
        limit = int(args['ExpressionAttributeValues'][':limit']['N'])
        with self.lock:
            count = self.calls.get(key, 0)
            if count >= limit:
                raise ConditionalFailure()
            self.calls[key] = count + 1


def test_atomic_admissions_stop_at_limit_across_concurrent_requests(monkeypatch):
    monkeypatch.setenv('WATTDOCKET_MUSE_QUOTA_TABLE', 'quota-test')
    monkeypatch.setenv('WATTDOCKET_MUSE_DAILY_LIMIT', '100')
    counter = FakeAtomicCounter()
    now = datetime(2026, 11, 2, 23, 59, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=20) as workers:
        results = list(workers.map(lambda _: quota.claim_daily_attempt(now=now, client=counter), range(150)))
    assert results.count('ALLOWED') == 100
    assert results.count('DAILY_QUOTA_EXHAUSTED') == 50
    assert counter.calls == {'muse#2026-11-02': 100}
    assert quota.claim_daily_attempt(now=now + timedelta(minutes=1), client=counter) == 'ALLOWED'
    assert counter.calls['muse#2026-11-03'] == 1


@pytest.mark.parametrize('table,limit', [('', '100'), ('quota-test', '0'),
                                        ('quota-test', '-1'), ('quota-test', 'invalid')])
def test_missing_or_invalid_configuration_fails_closed(monkeypatch, table, limit):
    monkeypatch.setenv('WATTDOCKET_MUSE_QUOTA_TABLE', table)
    monkeypatch.setenv('WATTDOCKET_MUSE_DAILY_LIMIT', limit)
    assert quota.claim_daily_attempt(client=FakeAtomicCounter()) == 'QUOTA_UNAVAILABLE'


def test_counter_or_sdk_failure_fails_closed(monkeypatch):
    monkeypatch.setenv('WATTDOCKET_MUSE_QUOTA_TABLE', 'quota-test')
    class Broken:
        def update_item(self, **_):
            raise RuntimeError('secret error detail')
    assert quota.claim_daily_attempt(client=Broken()) == 'QUOTA_UNAVAILABLE'
    monkeypatch.setattr(quota, '_client', lambda: (_ for _ in ()).throw(RuntimeError('unavailable')))
    assert quota.claim_daily_attempt() == 'QUOTA_UNAVAILABLE'
