# SPDX-License-Identifier: Apache-2.0

"""Workflow Engine main file."""

import io
import json
import os
import sys
import zipfile
from base64 import b64decode
from configparser import BasicInterpolation, ConfigParser
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse, urlunparse

import luigi.server
import sh
import tornado.web
from entity_management.core import DataDownload, WorkflowExecution
from sh import ErrorReturnCode
from tornado.httpclient import AsyncHTTPClient

from bbp_workflow_svc import __version__ as VERSION
from bbp_workflow_svc.auth import KEYCLOAK, SESSION_ID, KeycloakAuthHandler
from bbp_workflow_svc.settings import DEBUG, L

WORKFLOWS_PATH = Path(os.getenv("WORKFLOWS_PATH", "."))

LUIGI_CFG_PATH = Path("/home/bbp-workflow/luigi.cfg")
LOGGING_CFG_PATH = Path("/home/bbp-workflow/logging.cfg")

IDLE_TIMEOUT = 200


def _zip_files(files, cfg_name):
    """Zip files from POST request and extract nexus info from cfg name.

    Returns:
        Zip file buffer and nexus instance, org, proj.
    """
    buf = io.BytesIO()
    kg_params = {}
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for _, values in files.items():
            for file_ in values:
                archive.writestr(file_["filename"], file_["body"])
                if file_["filename"] == cfg_name:
                    cfg = ConfigParser(interpolation=BasicInterpolation())
                    cfg.read_string(file_["body"].decode())
                    kg_params |= {
                        "NEXUS_BASE": cfg.get(
                            "DEFAULT", "kg-base", fallback=os.getenv("NEXUS_BASE")
                        ),
                        "NEXUS_ORG": cfg.get("DEFAULT", "kg-org", fallback=os.getenv("NEXUS_ORG")),
                        "NEXUS_PROJ": cfg.get("DEFAULT", "kg-proj", fallback=None),
                        "NEXUS_NO_PROV": cfg.get("DEFAULT", "kg-no-prov", fallback=None),
                    }
    buf.seek(0)
    return buf, kg_params


def _dump_files(buf, dest: Path) -> None:
    """Write zipped files from buf to dest."""
    with zipfile.ZipFile(buf, mode="r") as archive:
        archive.extractall(dest)
    buf.seek(0)


def _register_workflow(buf, env, timestamp, module_name, task_name, cfg_name):
    # pylint: disable=too-many-positional-arguments
    """Register workflow execution in nexus."""
    zip_name = f"{timestamp}.zip"
    base, org, proj = env["NEXUS_BASE"], env["NEXUS_ORG"], env["NEXUS_PROJ"]
    token = KEYCLOAK.refresh_token(env["NEXUS_TOKEN"])["access_token"]
    data = DataDownload.from_file(
        buf,
        name=zip_name,
        content_type="application/zip",
        base=base,
        org=org,
        proj=proj,
        use_auth=token,
    )
    buf.seek(0)
    workflow = WorkflowExecution(
        name=f"{module_name}.{task_name}",
        module=module_name,
        task=task_name,
        version=VERSION,
        parameters=None,
        configFileName=cfg_name,
        distribution=data,
        status="Running",
    )
    workflow = workflow.publish(base=base, org=org, proj=proj, use_auth=token)
    return workflow.get_id(), workflow.get_url()


def _reg_prov(buf, env, timestamp, module, task, cfg_name):
    # pylint: disable=too-many-positional-arguments
    """Register workflow execution entity and update env with the link to it."""
    if "NEXUS_NO_PROV" not in env and "NEXUS_PROJ" in env:
        L.debug("Registering workflow provenance...")
        id_, url = _register_workflow(buf, env, timestamp, module, task, cfg_name)
        L.info("WORKFLOW LINK: %s", url)
        env["NEXUS_WORKFLOW"] = id_
        return url
    else:
        L.warning("Workflow provenance will not be registered!")
    return None


def _workflow(env):
    return (
        env.get("NEXUS_BASE"),
        env.get("NEXUS_ORG"),
        env.get("NEXUS_PROJ"),
        env.get("NEXUS_WORKFLOW"),
    )


def _update_workflow_status(env, status):
    base, org, proj, workflow_id = _workflow(env)
    if workflow_id:
        token = KEYCLOAK.refresh_token(env["NEXUS_TOKEN"])["access_token"]
        workflow = WorkflowExecution.from_id(
            workflow_id, base=base, org=org, proj=proj, use_auth=token
        )
        workflow.evolve(status=status, endedAtTime=datetime.utcnow()).publish(use_auth=token)


def _run_worker(cmd_params, env, key):
    new_env = os.environ.copy()
    new_env |= env
    try:
        with _ssh_agt(key) as ssh_auth_sock:
            sh.luigi(*cmd_params, _env=new_env | ssh_auth_sock, _out=sys.stdout, _err=sys.stderr)
        _update_workflow_status(env, "Done")
    except ErrorReturnCode:
        _update_workflow_status(env, "Failed")
        raise


def _launch(buf, env, key, timestamp, module_name, task_name, cfg_name):
    # pylint: disable=too-many-positional-arguments
    """Launch the luigi task."""
    url = _reg_prov(buf, env, timestamp, module_name, task_name, cfg_name)
    workflows_path = WORKFLOWS_PATH / timestamp
    _dump_files(buf, workflows_path)
    env["PYTHONPATH"] = str(workflows_path)
    if cfg_name:
        env["LUIGI_CONFIG_PATH"] = str(workflows_path / cfg_name)

    cmd_params = ["--logging-conf-file", LOGGING_CFG_PATH, "--module", module_name, task_name]
    L.info("Launching: %s", cmd_params)

    Thread(target=_run_worker, args=(cmd_params, env, key)).start()

    return url


class VersionHandler(tornado.web.RequestHandler):
    """Handle version requests."""

    # pylint: disable=abstract-method

    def get(self, *_, **__):
        """Get version."""
        assert SESSION_ID == self.get_cookie("sessionid")
        self.write(VERSION)


class HealthzHandler(tornado.web.RequestHandler):
    """Handle healthz requests."""

    # pylint: disable=abstract-method

    def get(self, *_, **__):
        """Get."""
        self.set_status(204)


class DashboardHandler(tornado.web.RequestHandler):
    """Proxy luigi dashboard."""

    # pylint: disable=abstract-method

    async def get(self, *_, **__):
        """Get."""
        client = AsyncHTTPClient()
        url = self.request.uri
        parsed_url = urlparse(url)
        if parsed_url.path == "/dashboard/":
            path = "/static/visualiser/index.html"
        else:
            path = parsed_url.path.replace("/dashboard/", "/static/visualiser/")
        url = urlunparse(
            (
                "http",
                "127.0.0.1:8082",
                path,
                parsed_url.params,
                parsed_url.query,
                parsed_url.fragment,
            )
        )
        response = await client.fetch(url)
        self.set_status(response.code, response.reason)
        for header, v in response.headers.get_all():
            if header not in (
                "Content-Length",
                "Transfer-Encoding",
                "Content-Encoding",
                "Connection",
            ):
                self.add_header(header, v)

        if response.body:
            self.set_header("Content-Length", len(response.body))
            self.write(response.body)
        self.finish()


@contextmanager
def _ssh_agt(key):
    """Will update env with SSH_AUTH_SOCK value."""
    ssh_agent_proc = sh.ssh_agent("-D", _bg=True, _iter=True, _ok_code=[0, 2])
    line = next(ssh_agent_proc, None)
    env = os.environ.copy()
    ssh_auth_sock = dict([line.split(";")[0].split("=")])  # put ssh_auth_sock var into env
    env |= ssh_auth_sock
    sh.ssh_add("-", _env=env, _in=key, _out=sys.stdout, _err=sys.stderr)
    try:
        yield ssh_auth_sock
    finally:
        if ssh_agent_proc.is_alive():
            ssh_agent_proc.terminate()
        for line in ssh_agent_proc:  # drain output
            pass


class ApiLaunchHandler(tornado.web.RequestHandler):
    """Launch task through API."""

    # pylint: disable=abstract-method

    def post(self, task):
        """Handle post."""
        if SESSION_ID != self.get_cookie("sessionid"):
            self.set_status(403)
            return
        L.info("API launch: %s", task)
        env = {}
        if DEBUG:
            env |= {"DEBUG": "True"}
        module_name, task_name = task.rsplit(".", 1)
        print(f"{module_name=} {task_name=}")
        cfg_name = self.get_body_argument("cfg_name", None)
        print(f"{cfg_name=}")
        timestamp = f"{datetime.now():%Y-%m-%d_%H-%M-%S.%f}"
        # FIXME
        buf, kg_params = _zip_files(self.request.files, cfg_name)
        print(f"{kg_params=}")
        env |= {k: v for k, v in kg_params.items() if v is not None}
        if "Authorization" in self.request.headers:
            key = b64decode(self.request.headers["Authorization"].encode()).decode()
        else:
            key = None
        print()
        workflow_execution = _launch(buf, env, key, timestamp, module_name, task_name, cfg_name)
        if workflow_execution:
            self.write(workflow_execution)
        self.set_status(200)
        return


async def idle_culling(call_later_fn):
    """Stop if idle."""
    # check worker_list for idle culling
    client = AsyncHTTPClient()
    try:
        response = await client.fetch("http://127.0.0.1:8082/api/worker_list")
        worker_list = json.loads(response.body)["response"]
        L.info("Worker list: %s", worker_list)
        if not worker_list:
            luigi.server.stop()
        call_later_fn(IDLE_TIMEOUT, idle_culling, call_later_fn)
    except Exception:
        luigi.server.stop()


def main():
    """Start the workflow launcher."""
    app = tornado.web.Application(
        [
            ("/auth/", KeycloakAuthHandler),
            (r"/launch/([^/]+)/", ApiLaunchHandler),
            ("/dashboard/.*", DashboardHandler),
            ("/api/.*", DashboardHandler),
            ("/version/", VersionHandler),
            ("/healthz/", HealthzHandler),
        ],
    )
    app.listen(8100)

    call_later_fn = tornado.ioloop.IOLoop.current().call_later
    call_later_fn(IDLE_TIMEOUT, idle_culling, call_later_fn)
    luigi.server.run(address="127.0.0.1")


if __name__ == "__main__":
    main()
