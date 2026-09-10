"""Read-only Tenrai feasibility probe; does not import or modify AniBase runtime.

Run: python tools/probe_tenrai.py
"""
import json
import time
import requests


def main():
    paths = [
        '/anime?q=Sousou%20no%20Frieren&limit=3',
        '/anime/52991/full',
        '/anime/52991/characters',
        '/anime/52991/recommendations',
        '/schedules?filter=thursday&limit=2',
        '/producers?q=Madhouse&limit=2',
        '/anime?producers=11&limit=2',
        '/people/126/full',
    ]
    results = []
    with requests.Session() as session:
        for path in paths:
            started = time.monotonic()
            result = {'path': path}
            try:
                response = session.get('https://api.tenrai.org/v1' + path, timeout=(5, 20))
                result.update(status=response.status_code,
                              seconds=round(time.monotonic() - started, 2))
                payload = response.json()
                data = payload.get('data') if isinstance(payload, dict) else None
                result['valid_data'] = response.ok and isinstance(data, (dict, list)) and bool(data)
                sample = data[0] if isinstance(data, list) and data else data
                result['count'] = len(data) if isinstance(data, list) else None
                result['sample'] = sample
            except (requests.RequestException, ValueError) as error:
                result.update(valid_data=False, error=str(error))
            results.append(result)
            time.sleep(0.6)
    print(json.dumps(results, indent=2, ensure_ascii=True))
    return 0 if all(result['valid_data'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
