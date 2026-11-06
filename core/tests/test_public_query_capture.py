from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
import json

from wattdocket.production.fetch import CaptureFetcher
from wattdocket.production.storage import LocalStore, digest, json_bytes


def test_public_post_queries_archive_distinct_bodies_and_never_share_conditional_cache(tmp_path, monkeypatch):
    calls = []
    class Response(BytesIO):
        def __init__(self, raw, url):
            super().__init__(raw)
            self.url = url
            self.headers = Message()
            self.headers['Content-Type'] = 'application/json'
            self.headers['ETag'] = 'same-publisher-etag'
    def request(req, **kwargs):
        assert req.get_method() == 'POST'
        assert 'If-none-match' not in req.headers
        payload = json.loads(req.data)
        calls.append(payload)
        return Response(json_bytes({'result':payload}),req.full_url)
    monkeypatch.setattr('urllib.request.urlopen',request)
    fetcher = CaptureFetcher(LocalStore(tmp_path),datetime(2026,11,4,tzinfo=timezone.utc))
    url='https://publisher.example/public-query'
    for value in [1,2,1]:
        assert fetcher.post_json(url,{'query':value}) == {'result':{'query':value}}
    assert len(calls) == 3 and len(fetcher.receipts) == 2
    for receipt in fetcher.receipts.values():
        assert receipt['request_method'] == 'POST'
        assert receipt['request_body_sha256'] == digest(json_bytes(receipt['request_body']))
        assert receipt['raw_key'] == 'raw/'+receipt['sha256']+'.gz'
    assert len(list((tmp_path/'http').glob('*.json'))) == 2
