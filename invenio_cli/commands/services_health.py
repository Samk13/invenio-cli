# SPDX-FileCopyrightText: 2020 CERN.
# SPDX-FileCopyrightText: 2023 ULB Münster.
# SPDX-FileCopyrightText: 2024 Graz University of Technology.
# SPDX-License-Identifier: MIT

"""Invenio module to ease the creation and management of applications."""

#####
# IMPORTANT NOTE: If you are going to modify any code here.
# Check `docker-service-cli` since the original code belongs there,
# and any bug might have already been fixed there.
# The reason for the copy-paste was to simplify the complexity of the
# integration. Might be integrated in the future when `docker-services-cli`
# reaches a higher maturity level.
#####

import time

import yaml
from docker_services_cli.services import rustfs_create_default_bucket

from ..helpers.env import env
from ..helpers.process import ProcessResponse, run_cmd


class ServicesHealthCommands(object):
    """Services status commands."""

    @classmethod
    def search_healthcheck(cls, *args, **kwargs):
        """Open/Elasticsearch healthcheck."""
        host = kwargs["search_host"]
        port = kwargs["search_port"]
        return run_cmd(
            ["curl", "-f", f"{host}:{port}/_cluster/health?wait_for_status=yellow"]
        )

    @classmethod
    def postgresql_healthcheck(cls, *args, **kwargs):
        """Postgresql healthcheck."""
        filepath = kwargs["filepath"]

        return run_cmd(
            [
                "docker",
                "compose",
                "--file",
                filepath,
                "exec",
                "-T",
                "db",
                "sh",
                "-c",
                "pg_isready",
            ]
        )

    @classmethod
    def mysql_healthcheck(cls, *args, **kwargs):
        """Mysql healthcheck."""
        filepath = kwargs["filepath"]
        password = kwargs["project_shortname"]

        return run_cmd(
            [
                "docker",
                "compose",
                "--file",
                filepath,
                "exec",
                "-T",
                "db",
                "bash",
                "-c",
                f'mysql -p{password} -e "select Version();"',
            ]
        )

    @classmethod
    def redis_healthcheck(cls, *args, **kwargs):
        """Redis healthcheck."""
        filepath = kwargs["filepath"]

        return run_cmd(
            [
                "docker",
                "compose",
                "--file",
                filepath,
                "exec",
                "-T",
                "cache",
                "sh",
                "-c",
                "redis-cli ping",
                "|",
                "grep 'PONG'",
                "&>/dev/null;",
            ]
        )

    @classmethod
    def s3_healthcheck(cls, *args, **kwargs):
        """Initialize a Compose-managed RustFS bucket for browser uploads."""
        response = run_cmd(
            ["docker", "compose", "--file", kwargs["filepath"], "config"]
        )
        if response.status_code != 0:
            return response

        config = yaml.safe_load(response.output) or {}
        service = config.get("services", {}).get("s3", {})
        image = service.get("image", "").split("@")[0].split(":")[0]
        if image != "rustfs/rustfs":
            # External S3 and legacy MinIO setups are not managed here.
            return ProcessResponse()

        port = next(
            (
                port
                for port in service.get("ports", [])
                if port.get("target") == 9000 and port.get("published")
            ),
            None,
        )
        if port is None:
            return ProcessResponse(
                error="RustFS must publish its S3 API port for local setup.",
                status_code=1,
            )
        host = port.get("host_ip", "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        if ":" in host:
            host = f"[{host}]"
        endpoint = f"http://{host}:{port['published']}"
        response = run_cmd(["curl", "-f", f"{endpoint}/health"])
        if response.status_code != 0:
            return response

        environment = service.get("environment", {})
        access_key = environment.get("RUSTFS_ACCESS_KEY")
        secret_key = environment.get("RUSTFS_SECRET_KEY")
        if not access_key or not secret_key:
            return ProcessResponse(
                error="RustFS credentials must be configured in Compose.",
                status_code=1,
            )
        with env(
            S3_ENDPOINT_URL=endpoint,
            S3_ACCESS_KEY_ID=access_key,
            S3_SECRET_ACCESS_KEY=secret_key,
        ):
            ready = rustfs_create_default_bucket(verbose=kwargs["verbose"])
        return ProcessResponse(
            status_code=0 if ready else 1,
            error=None if ready else "Could not initialize the RustFS default bucket.",
        )

    @classmethod
    def wait_for_service(
        cls,
        service,
        project_shortname,
        print_func,
        filepath="docker-services.yml",
        max_retries=6,
        verbose=False,
        search_host="localhost",
        search_port="9200",
    ):
        """Wait for the given service to be up."""
        if service not in HEALTHCHECKS:
            raise RuntimeError(
                f"{service} not recognized. Available services: {HEALTHCHECKS.keys()}"
            )

        exp_backoff_time = 2
        try_ = 0
        check = HEALTHCHECKS[service]
        check_func = check["func"]
        initial_delay = check.get("initial_delay", 0)
        wait_initial_delay = initial_delay > 0
        ready = False

        while not ready and try_ < max_retries:
            response = check_func(
                filepath=filepath,
                verbose=verbose,
                project_shortname=project_shortname,
                search_host=search_host,
                search_port=search_port,
            )
            ready = response.status_code == 0

            if not ready:
                is_first_check = try_ == 0

                # some services might be particularly slow to start up
                if is_first_check and wait_initial_delay:
                    print_func(
                        f"{service} starting up, checking in {initial_delay}s...",
                    )
                    time.sleep(initial_delay)
                else:
                    print_func(
                        f"{service} not ready at {try_+1} retries, waiting "
                        + f"{exp_backoff_time}s...",
                    )
                    time.sleep(exp_backoff_time)
                    exp_backoff_time *= 2

                try_ += 1

        return ready


HEALTHCHECKS = {
    "s3": {
        "func": ServicesHealthCommands.s3_healthcheck,
        "initial_delay": 0,
    },
    "search": {
        "func": ServicesHealthCommands.search_healthcheck,
        "initial_delay": 15,  # search cluster can be particularly slow to start
    },
    "postgresql": {
        "func": ServicesHealthCommands.postgresql_healthcheck,
        "initial_delay": 0,
    },
    "mysql": {
        "func": ServicesHealthCommands.mysql_healthcheck,
        "initial_delay": 0,
    },
    "redis": {
        "func": ServicesHealthCommands.redis_healthcheck,
        "initial_delay": 0,
    },
}
"""Health check functions module path, as string."""
