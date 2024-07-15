# SPDX-License-Identifier: Apache-2.0

"""Workflow Engine main file."""

import io
import os
import sys
import zipfile
from configparser import BasicInterpolation, ConfigParser
from datetime import datetime
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse, urlunparse

import luigi.server
import tornado.web
from entity_management.core import DataDownload, WorkflowExecution
from sh import ErrorReturnCode, echo  # pylint: disable=no-name-in-module
from tornado.httpclient import AsyncHTTPClient

from bbp_workflow_svc import __version__ as VERSION
from bbp_workflow_svc.auth import KEYCLOAK, SESSION_ID, KeycloakAuthHandler
from bbp_workflow_svc.settings import DEBUG, L

PATH_PREFIX = os.environ["HPC_PATH_PREFIX"]
DATA_PREFIX = os.environ["HPC_DATA_PREFIX"]
USER = os.environ["USER"]
WORKFLOWS_PATH = Path(PATH_PREFIX) / USER / "workflows"

LUIGI_CFG_PATH = Path("/home/bbp-workflow/luigi.cfg")
LOGGING_CFG_PATH = Path("/home/bbp-workflow/logging.cfg")


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


def _run_worker(cmd, env):
    new_env = os.environ.copy()
    new_env |= env
    try:
        # pylint: disable=unexpected-keyword-arg,too-many-function-args
        # p = ssh(HPC_HEAD_NODE, cmd, _env=new_env, _out=sys.stdout, _err=sys.stderr, _bg=True)
        # p.wait()
        echo(cmd, _env=new_env, _out=sys.stdout, _err=sys.stderr)
        _update_workflow_status(env, "Done")
    except ErrorReturnCode:
        _update_workflow_status(env, "Failed")
        raise


def _launch(buf, env, timestamp, module_name, task_name, cfg_name):
    # pylint: disable=too-many-positional-arguments
    """Launch the luigi task."""
    url = _reg_prov(buf, env, timestamp, module_name, task_name, cfg_name)
    workflows_path = WORKFLOWS_PATH / timestamp
    env["PYTHONPATH"] = str(workflows_path)
    if cfg_name:
        env["LUIGI_CONFIG_PATH"] = str(workflows_path / cfg_name)

    cmd = f"luigi --logging-conf-file {workflows_path / LOGGING_CFG_PATH.name} "
    cmd += f'--module {module_name} {task_name}"'
    L.info("Launching: %s", cmd)

    Thread(target=_run_worker, args=(cmd, env)).start()

    return url


class VersionHandler(tornado.web.RequestHandler):
    """Handle version requests."""

    # pylint: disable=abstract-method

    def get(self, *_, **__):
        """Get version."""
        assert SESSION_ID == self.get_cookie("sessionid")
        self.write(VERSION)


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


class ApiLaunchHandler(tornado.web.RequestHandler):
    """Launch task through API."""

    # pylint: disable=abstract-method

    def get_current_user(self):
        return self.get_cookie("sessionid")

    def post(self, task):
        """Handle post."""
        L.info("API launch: %s", task)
        if self.current_user:
            env = {}
            if DEBUG:
                env |= {"DEBUG": "True"}
            module_name, task_name = task.rsplit(".", 1)
            cfg_name = self.get_body_argument("cfg_name", None)
            timestamp = f"{datetime.now():%Y-%m-%d_%H-%M-%S.%f}"
            buf, kg_params = _zip_files(self.request.files, cfg_name)
            env |= {k: v for k, v in kg_params.items() if v is not None}
            workflow_execution = _launch(buf, env, timestamp, module_name, task_name, cfg_name)
            if workflow_execution:
                self.write(workflow_execution)
            self.set_status(200)
            return

        self.set_status(403)


def idle_culling(_call_later_fn):
    """Stop if idle."""
    # check worker_list for idle culling
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
        ],
    )
    app.listen(8100)

    call_later_fn = tornado.ioloop.IOLoop.current().call_later
    call_later_fn(200, idle_culling, call_later_fn)
    luigi.server.run(address="127.0.0.1")


if __name__ == "__main__":
    main()
