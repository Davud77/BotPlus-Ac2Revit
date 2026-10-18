import json
import urllib.request
from pathlib import Path
from .jsonio import write_json


class ApiError(RuntimeError):
    pass


class TapirClient:
    """Read-only commands, serial requests; every response is archived before parsing."""
    def __init__(self, port=19723, archive=None, timeout=180, resume=False):
        self.url = f'http://127.0.0.1:{port}'
        self.timeout = timeout
        self.archive = Path(archive) if archive else None
        self.sequence = 0
        self.cache = {}
        self.last_archive = None
        if self.archive:
            self.archive.mkdir(parents=True, exist_ok=True)
            for path in self.archive.glob('*.json'):
                self.sequence = max(self.sequence, int(path.name.split('_')[0]))
                if resume:
                    item = json.loads(path.read_text(encoding='utf-8'))
                    if item['response'].get('succeeded'):
                        self.cache[json.dumps(item['request'], sort_keys=True)] = path
                    del item

    def call(self, command, parameters=None, tapir=True):
        if tapir:
            payload = {'command': 'API.ExecuteAddOnCommand', 'parameters': {
                'addOnCommandId': {'commandNamespace': 'TapirCommand', 'commandName': command}}}
            if parameters is not None:
                payload['parameters']['addOnCommandParameters'] = parameters
        else:
            payload = {'command': 'API.' + command}
            if parameters is not None:
                payload['parameters'] = parameters
        cache_key = json.dumps(payload, sort_keys=True)
        self.last_archive = self.cache.get(cache_key) if command not in ('GetProjectInfo', 'GetStories', 'GetProductInfo', 'GetAddOnVersion') else None
        result = None
        if self.last_archive:
            with self.last_archive.open(encoding='utf-8') as stream:
                result = json.load(stream)['response']
        if result is None:
            request = urllib.request.Request(self.url, data=json.dumps(payload).encode('utf-8'),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
            self.sequence += 1
            if self.archive:
                path = self.archive / f'{self.sequence:06d}_{command}.json'
                write_json(path, {'request': payload, 'response': result}, compact=True)
                self.last_archive = path
        if result.get('succeeded') is not True:
            raise ApiError(f'{command}: {result.get("error", result)}')
        body = result.get('result', {})
        if tapir:
            body = body.get('addOnCommandResponse', {})
        if 'error' in body:
            raise ApiError(f'{command}: {body["error"]}')
        return body


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start+size]


def aligned(response, key, inputs):
    """Tapir responses are positional, including errors. Never filter before zipping."""
    rows = response.get(key)
    if not isinstance(rows, list) or len(rows) != len(inputs):
        raise ApiError(f'{key}: response count differs from request ({len(inputs)})')
    return zip(inputs, rows)
