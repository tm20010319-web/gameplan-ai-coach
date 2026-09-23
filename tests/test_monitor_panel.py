from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from gameplan.web.monitor_app import app
from gameplan.monitoring.overlay import panel_url


def test_panel_url_only_opens_valid_local_monitor():
    assert panel_url(8767, 'a' * 16).startswith('http://127.0.0.1:8767/monitor/panel?')
    for args in [(80,), (8767, 'https://example.com'), (8767, None, 'command')]:
        with pytest.raises(ValueError):
            panel_url(*args)


def test_launch_requires_same_origin_local_client_and_existing_source():
    with TestClient(app, base_url='http://127.0.0.1:8767', client=('127.0.0.1', 5050)) as client, \
         patch('gameplan.monitoring.panel_launcher.open_panel', return_value={'opened': True}) as launch, \
         patch('gameplan.monitoring.screen_capture.sources', return_value=[{'id': 'a' * 16}]):
        assert client.get('/monitor/panel').status_code == 200
        payload = {'source_id': 'a' * 16, 'region': 'full'}
        assert client.post('/api/monitor/panel/open', json=payload, headers={'origin': 'http://other.test'}).status_code == 403
        assert client.post('/api/monitor/panel/open', json={'source_id': 'b' * 16}).status_code == 409
        launch.assert_not_called()
        assert client.post('/api/monitor/panel/open', json=payload).status_code == 200
        launch.assert_called_once_with(8767, 'a' * 16, 'full')
