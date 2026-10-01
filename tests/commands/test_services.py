# SPDX-FileCopyrightText: 2026 KTH Royal Institute of Technology.
# SPDX-License-Identifier: MIT

"""Service startup tests."""

from unittest.mock import Mock

import pytest

from invenio_cli.commands.services import ServicesCommands
from invenio_cli.commands.services_health import ServicesHealthCommands


@pytest.mark.parametrize(
    "storage,expected",
    [
        ("local", ["redis", "postgresql", "search"]),
        ("S3", ["redis", "postgresql", "search", "s3"]),
    ],
)
def test_ensure_containers_checks_storage(monkeypatch, storage, expected):
    """Initialize S3 storage only when it is selected in the project."""
    config = Mock()
    config.get_project_shortname.return_value = "test-project"
    config.get_instance_path.return_value = None
    config.get_db_type.return_value = "postgresql"
    config.get_file_storage.return_value = storage
    helper = Mock()
    wait = Mock(return_value=True)
    monkeypatch.setattr(ServicesHealthCommands, "wait_for_service", wait)
    response = ServicesCommands(
        config, docker_helper=helper
    ).ensure_containers_running()
    assert response.status_code == 0
    assert [call.args[0] for call in wait.call_args_list] == expected
    helper.start_containers.assert_called_once_with()


def test_ensure_containers_s3_failure(monkeypatch):
    """Fail startup when the S3 bucket cannot be initialized."""
    config = Mock()
    config.get_instance_path.return_value = None
    config.get_db_type.return_value = "postgresql"
    config.get_file_storage.return_value = "S3"
    wait = Mock(side_effect=[True, True, True, False])
    monkeypatch.setattr(ServicesHealthCommands, "wait_for_service", wait)
    response = ServicesCommands(
        config, docker_helper=Mock()
    ).ensure_containers_running()
    assert response.status_code == 1
    assert response.error == "Unable to boot up s3"
