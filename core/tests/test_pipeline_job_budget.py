"""Edition follow-ups share the job deadline, including before any fresh I/O."""
from datetime import datetime, timedelta, timezone

import pytest

from wattdocket.production import pipeline
from wattdocket.production.fetch import stamp
from wattdocket.production.models import SourceResult
from wattdocket.production.storage import LocalStore, digest, json_bytes, read_json

NOW = datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize('annual_elapsed', [3, 10, 11])
def test_annual_edition_followup_reuses_remaining_job_budget_before_fetcher_construction(
        tmp_path, monkeypatch, annual_elapsed):
    archive = LocalStore(tmp_path / 'archive')
    public = LocalStore(tmp_path / 'public')
    old_stamp = stamp(NOW - timedelta(days=1))
    old_result = SourceResult('egrid_geometry', 'Boundaries', 'https://publisher.example/boundaries',
                              {'data_year': 2023, 'marker': 'original captured boundaries'}, 1).as_dict()
    old_geometry = {'id': 'egrid_geometry', 'label': 'Boundaries', 'record_count': 1,
                    'result': old_result, 'result_sha256': digest(json_bytes(old_result)),
                    'last_attempt_at': old_stamp, 'last_success_at': old_stamp,
                    'retrieved_at': old_stamp, 'status': 'current', 'error': None}
    archive.put('state/sources/egrid_geometry.json', json_bytes(old_geometry))
    policies = {sid: {'required': True, 'minimum': 1, 'interval': 30 * 86400,
                      'stale': 45 * 86400, 'fetcher': {'source_budget_seconds': 90}}
                for sid in ('egrid', 'egrid_geometry')}
    monkeypatch.setattr(pipeline, 'POLICIES', policies)
    monkeypatch.setattr(pipeline, 'MAX_COLLECTION_SECONDS', 10)
    clock = [100.0]
    monkeypatch.setattr(pipeline.time, 'monotonic', lambda: clock[0])
    fetcher_budgets, network_attempts, deadlines, built_states = [], [], [], []

    class RecordingFetcher:
        def __init__(self, store, now, **options):
            self.receipts = {}
            fetcher_budgets.append(options['source_budget_seconds'])

        def get_bytes(self, url):
            network_attempts.append(url)
            assert clock[0] < 110.0, 'No new upstream request may begin after the shared deadline'
            return b'captured evidence'

    original_collect_one = pipeline.collect_one

    def collect_with_recording_fetcher(sid, adapter, store, now, **options):
        # Exercise collect_one's real deadline gate. Only its transport factory
        # is replaced, so a missing deadline on the follow-up cannot be hidden.
        deadlines.append((sid, options.get('deadline')))
        return original_collect_one(sid, adapter, store, now,
                                    fetcher_factory=RecordingFetcher, **options)

    monkeypatch.setattr(pipeline, 'collect_one', collect_with_recording_fetcher)

    def annual(fetcher, **kwargs):
        fetcher.get_bytes('https://publisher.example/annual')
        clock[0] += annual_elapsed
        return SourceResult('egrid', 'Annual', 'https://publisher.example/annual',
                            {'rates': {'TEST': {'data_year': 2024}}}, 1)

    def geometry(fetcher, **kwargs):
        fetcher.get_bytes('https://publisher.example/boundaries')
        return SourceResult('egrid_geometry', 'Boundaries', 'https://publisher.example/boundaries',
                            {'data_year': 2024}, 1)

    def capture_build(states, *args):
        built_states.append(states)
        return {'data.json': b'{}'}, {}

    monkeypatch.setattr(pipeline, 'build_release', capture_build)
    report = pipeline.run(archive, public, now=NOW, selected=['egrid'],
                          adapter_map={'egrid': annual, 'egrid_geometry': geometry}, promote=False)
    assert deadlines == [('egrid', 110.0), ('egrid_geometry', 110.0)]
    assert len(built_states) == 1
    final_geometry = built_states[0]['egrid_geometry']
    assert read_json(archive, 'state/sources/egrid_geometry.json') == final_geometry
    if annual_elapsed < 10:
        assert fetcher_budgets == [10.0, 7.0]
        assert network_attempts == ['https://publisher.example/annual', 'https://publisher.example/boundaries']
        assert final_geometry['result']['data']['data_year'] == 2024
        assert report['status'] == 'succeeded'
    else:
        # An exhausted budget must prevent even constructing another fetcher,
        # not merely time out an already-started geometry network request.
        assert fetcher_budgets == [10.0]
        assert network_attempts == ['https://publisher.example/annual']
        assert final_geometry['result'] == old_result
        assert final_geometry['result_sha256'] == old_geometry['result_sha256']
        assert final_geometry['last_success_at'] == old_stamp
        assert final_geometry['retrieved_at'] == old_stamp
        assert final_geometry['last_attempt_at'] == stamp(NOW)
        assert final_geometry['status'] == 'stale'
        assert 'Job collection deadline exhausted' in final_geometry['error']
        assert final_geometry['failed_attempt_receipts'] == []
        assert report['status'] == 'degraded'
        live_status = read_json(public, 'data/pipeline-status.json')
        assert any(source['id'] == 'egrid_geometry' and source['error'] == final_geometry['error']
                   for source in live_status['sources'])


@pytest.mark.parametrize('sid', [
    'nyc_benchmarking', 'seattle_benchmarking', 'google_disclosure_identity',
    'meta_disclosure_identity', 'municipal_disclosure_identity',
])
def test_expansion_collectors_have_registered_optional_bounded_refresh_policies(sid):
    from wattdocket.production.sources_municipal_reporting import collect_nyc_benchmarking, collect_seattle_benchmarking
    from wattdocket.production.identity_google_expansion import collect_google_identity
    from wattdocket.production.identity_meta_expansion import collect_meta_identity
    from wattdocket.production.identity_municipal_expansion import collect_municipal_identity
    expected = {'nyc_benchmarking': collect_nyc_benchmarking, 'seattle_benchmarking': collect_seattle_benchmarking,
                'google_disclosure_identity': collect_google_identity, 'meta_disclosure_identity': collect_meta_identity,
                'municipal_disclosure_identity': collect_municipal_identity}
    registered = pipeline.adapters()
    assert registered[sid] is expected[sid]
    assert pipeline.LABELS[sid]
    policy = pipeline.POLICIES[sid]
    assert policy['required'] is False
    assert policy['interval'] == 7 * 86400
    assert policy['stale'] == 30 * 86400
    assert policy['retry_interval'] == 86400
    assert 0 < policy['minimum'] <= policy['maximum'] <= 3000
    assert 0 < policy['max_normalized_bytes'] <= 8_000_000
    assert 0 < policy['fetcher']['max_bytes'] <= 16_000_000
    assert 0 < policy['fetcher']['source_budget_seconds'] <= min(90, pipeline.MAX_COLLECTION_SECONDS)
    assert policy.get('allow_partial_evidence', False) is sid.endswith('_disclosure_identity')
