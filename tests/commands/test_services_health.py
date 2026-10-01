# SPDX-FileCopyrightText: 2026 KTH Royal Institute of Technology.
# SPDX-License-Identifier: MIT

"""RustFS readiness and initialization tests."""

import os
from unittest.mock import Mock

import pytest
import yaml

from invenio_cli.commands import services_health
from invenio_cli.commands.services_health import ServicesHealthCommands
from invenio_cli.helpers.process import ProcessResponse


@pytest.fixture()
def compose_config():
    """Return resolved Compose configuration with project-specific credentials."""
    return {
        "services": {
            "s3": {
                "image": "rustfs/rustfs:1.0.0",
                "environment": {
                    "RUSTFS_ACCESS_KEY": "project-key",
                    "RUSTFS_SECRET_KEY": "project-secret",
                },
                "ports": [
                    {"target": 9000, "published": "9000", "host_ip": "127.0.0.1"}
                ],
            }
        }
    }


@pytest.mark.parametrize("ready", [True, False])
def test_s3_initializes_with_compose_credentials(monkeypatch, compose_config, ready):
    """Delegate initialization using local Compose credentials, not S3 defaults."""
    run = Mock(
        side_effect=[
            ProcessResponse(output=yaml.safe_dump(compose_config)),
            ProcessResponse(),
        ]
    )
    monkeypatch.setattr(services_health, "run_cmd", run)
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "external-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "external-secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://external.example.org")

    def initialize(verbose):
        assert verbose
        assert os.environ["S3_ACCESS_KEY_ID"] == "project-key"
        assert os.environ["S3_SECRET_ACCESS_KEY"] == "project-secret"
        assert os.environ["S3_ENDPOINT_URL"] == "http://127.0.0.1:9000"
        return ready

    initialize_mock = Mock(side_effect=initialize)
    monkeypatch.setattr(
        services_health, "rustfs_create_default_bucket", initialize_mock
    )
    response = ServicesHealthCommands.s3_healthcheck(
        filepath="docker-services.yml", verbose=True
    )
    assert response.status_code == (0 if ready else 1)
    initialize_mock.assert_called_once_with(verbose=True)
    assert run.call_args_list[0].args[0] == [
        "docker",
        "compose",
        "--file",
        "docker-services.yml",
        "config",
    ]
    assert run.call_args_list[1].args[0] == [
        "curl",
        "-f",
        "http://127.0.0.1:9000/health",
    ]
    assert os.environ["S3_ACCESS_KEY_ID"] == "external-key"
    assert os.environ["S3_SECRET_ACCESS_KEY"] == "external-secret"
    assert os.environ["S3_ENDPOINT_URL"] == "https://external.example.org"


@pytest.mark.parametrize("image", [None, "minio/minio:latest"])
def test_s3_skips_unmanaged_storage(monkeypatch, compose_config, image):
    """Do not modify external storage or legacy MinIO buckets."""
    if image is None:
        compose_config["services"].pop("s3")
    else:
        compose_config["services"]["s3"]["image"] = image
    run = Mock(return_value=ProcessResponse(output=yaml.safe_dump(compose_config)))
    initialize = Mock()
    monkeypatch.setattr(services_health, "run_cmd", run)
    monkeypatch.setattr(services_health, "rustfs_create_default_bucket", initialize)
    assert (
        ServicesHealthCommands.s3_healthcheck(
            filepath="compose.yml", verbose=False
        ).status_code
        == 0
    )
    assert run.call_count == 1
    initialize.assert_not_called()


@pytest.mark.parametrize("failure", ["config", "health", "credentials", "port"])
def test_s3_readiness_failure(monkeypatch, compose_config, failure):
    """Avoid initialization until configuration and the API are ready."""
    if failure == "credentials":
        compose_config["services"]["s3"]["environment"] = {}
    elif failure == "port":
        compose_config["services"]["s3"]["ports"] = []
    run = Mock(
        side_effect=[
            ProcessResponse(
                output=yaml.safe_dump(compose_config),
                status_code=1 if failure == "config" else 0,
            ),
            ProcessResponse(status_code=1 if failure == "health" else 0),
        ]
    )
    initialize = Mock()
    monkeypatch.setattr(services_health, "run_cmd", run)
    monkeypatch.setattr(services_health, "rustfs_create_default_bucket", initialize)
    assert (
        ServicesHealthCommands.s3_healthcheck(
            filepath="compose.yml", verbose=False
        ).status_code
        == 1
    )
    initialize.assert_not_called()


@pytest.mark.parametrize(
    "host,expected", [("0.0.0.0", "127.0.0.1"), ("::", "127.0.0.1"), ("::1", "[::1]")]
)
def test_s3_published_address(monkeypatch, compose_config, host, expected):
    """Use the published API port and a connectable host address."""
    compose_config["services"]["s3"]["ports"][0].update(host_ip=host, published="19000")
    run = Mock(
        side_effect=[
            ProcessResponse(output=yaml.safe_dump(compose_config)),
            ProcessResponse(),
        ]
    )
    monkeypatch.setattr(services_health, "run_cmd", run)
    monkeypatch.setattr(
        services_health, "rustfs_create_default_bucket", lambda **kwargs: True
    )
    assert (
        ServicesHealthCommands.s3_healthcheck(
            filepath="compose.yml", verbose=False
        ).status_code
        == 0
    )
    assert run.call_args.args[0] == ["curl", "-f", f"http://{expected}:19000/health"]


def test_s3_readiness_retries(monkeypatch):
    """Retry failed bucket initialization using the existing readiness workflow."""
    check = Mock(side_effect=[ProcessResponse(status_code=1), ProcessResponse()])
    monkeypatch.setitem(
        services_health.HEALTHCHECKS, "s3", {"func": check, "initial_delay": 0}
    )
    monkeypatch.setattr(services_health.time, "sleep", Mock())
    assert ServicesHealthCommands.wait_for_service(
        "s3", "project", Mock(), max_retries=2
    )
    assert check.call_count == 2
