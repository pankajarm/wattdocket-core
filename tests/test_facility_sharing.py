"""Public share pages must describe and open the same snapshot record."""
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from build_share_pages import build, edge_function, page  # noqa: E402


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = {}
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and ('property' in attrs or 'name' in attrs):
            self.tags[attrs.get('property', attrs.get('name'))] = attrs.get('content')
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.tags['canonical'] = attrs.get('href')


def fixture(identifier='echo:110031223771-1', name='A & B "Data"'):
    entry = {'id': identifier, 'name': name, 'address_line': '123 MAIN', 'city': 'ASHBURN', 'state': 'VA'}
    card = {'as_of': '2026-10-29T02:39:29Z', 'facility': {
        'id': identifier.replace(':', '_'), 'name': name, 'address': '123 MAIN', 'city': 'ASHBURN', 'state': 'VA'}}
    return entry, card


def test_preview_is_escaped_and_identifies_exact_public_record():
    entry, card = fixture()
    slug, text = page(entry, card)
    meta = Metadata(); meta.feed(text)
    assert slug == 'echo_110031223771-1'
    assert meta.tags['og:title'] == 'A & B "Data" | WattDocket'
    assert 'ASHBURN, VA' in meta.tags['og:description']
    assert '2026-10-29' in meta.tags['og:description']
    assert 'not facility measurements' in meta.tags['og:description']
    assert meta.tags['og:url'] == meta.tags['canonical'] == 'https://wattdocket.com/share/echo_110031223771-1.html'
    assert meta.tags['og:image'] == 'https://wattdocket.com/app/share-preview.png'
    assert '/app/index.html#/public/echo%3A110031223771-1' in text
    assert 'A &amp; B &quot;Data&quot;' in text


def test_generator_rejects_disagreement_and_never_injects_external_redirect(tmp_path):
    entry, card = fixture(name='<script>alert(1)</script>')
    _, text = page(entry, card)
    assert '<script>alert(1)</script>' not in text
    assert '&lt;script&gt;' in text
    card['facility']['name'] = 'Another facility'
    with pytest.raises(ValueError, match='disagree'):
        page(entry, card)
    with pytest.raises(ValueError, match='Invalid facility'):
        page({'id': 'echo:../evil'}, card)


def test_build_covers_index_including_siblings_and_alphanumeric_ids(tmp_path):
    index = ROOT / 'site/api/facilities'
    cards = ROOT / 'site/api/card'
    output = tmp_path / 'share'
    function = tmp_path / 'edge.js'
    assert build(index, cards, output, function) == len(json.loads(index.read_text()))
    assert (output / 'echo_110031223771-1.html').is_file()
    assert (output / 'echo_110031223771-2.html').is_file()
    assert (output / 'echo_ga0000001331300171.html').is_file()
    assert function.stat().st_size < 10 * 1024
    runner = '''const fs = require('fs');
const handler = new Function(fs.readFileSync(process.argv[1], 'utf8') + ';return handler')();
const req = (uri) => handler({request:{uri,headers:{host:{value:'wattdocket.com'}}}});
process.stdout.write(JSON.stringify(process.argv.slice(2).map(req)));'''
    paths = ['/share/echo_110031223771-1.html', '/share/echo_ga0000001331300171.html',
             '/share/echo_110031223771-99.html', '/share/echo_000000000000.html', '/share/evil']
    data = json.loads(subprocess.check_output(['node', '-e', runner, str(function), *paths], text=True))
    assert [r.get('statusCode', 200) for r in data] == [302, 302, 404, 404, 404]
    assert data[0]['headers']['location']['value'] == '/app/index.html#/public/echo%3A110031223771-1'
    assert data[1]['headers']['location']['value'] == '/app/index.html#/public/echo%3Aga0000001331300171'


def test_generated_edge_redirect_retains_raw_query():
    function = edge_function(['echo_110031223771-1'])
    runner = '''const handler = new Function(process.argv[1] + ';return handler')();
const request = {uri:'/share/echo_110031223771-1.html',headers:{host:{value:'www.wattdocket.com'}},rawQueryString:()=> 'a=%2B&a=2'};
process.stdout.write(JSON.stringify(handler({request})));'''
    response = json.loads(subprocess.check_output(['node', '-e', runner, function], text=True))
    assert response['statusCode'] == 308
    assert response['headers']['location']['value'] == 'https://wattdocket.com/share/echo_110031223771-1.html?a=%2B&a=2'
