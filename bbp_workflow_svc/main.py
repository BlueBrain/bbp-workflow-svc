# SPDX-License-Identifier: Apache-2.0

"""Workflow Engine main file."""

import asyncio
import io
import os
import sys
import zipfile
from configparser import BasicInterpolation, ConfigParser
from datetime import datetime
from pathlib import Path
from threading import Thread

import tornado.web
from cryptography.fernet import Fernet
from entity_management.core import DataDownload, WorkflowExecution
from sh import ErrorReturnCode, ssh  # pylint: disable=no-name-in-module

from bbp_workflow_svc import __version__ as VERSION
from bbp_workflow_svc.auth import CRYPT, KEYCLOAK, KeycloakAuthHandler
from bbp_workflow_svc.settings import BBP_WORKFLOW_SIF, DEBUG, L

PATH_PREFIX = os.environ["HPC_PATH_PREFIX"]
DATA_PREFIX = os.environ["HPC_DATA_PREFIX"]
USER = os.environ["USER"]
HPC_HEAD_NODE = os.environ["HPC_HEAD_NODE"]
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
        ssh(HPC_HEAD_NODE, cmd, _env=new_env, _out=sys.stdout, _err=sys.stderr)
        _update_workflow_status(env, "Done")
    except ErrorReturnCode:
        _update_workflow_status(env, "Failed")
        raise


def _sync_files(buf, workflows_path):
    # pylint: disable=unexpected-keyword-arg,too-many-function-args
    ssh(HPC_HEAD_NODE, f"mkdir -p {workflows_path.parent}")
    ssh(HPC_HEAD_NODE, f"mkdir {workflows_path}")
    is_custom_logging_cfg_file = False
    is_custom_luigi_cfg_file = False
    with zipfile.ZipFile(buf) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            ssh(HPC_HEAD_NODE, f"cat > {workflows_path / name}", _in=data)
            if name == LOGGING_CFG_PATH.name:
                is_custom_logging_cfg_file = True
            elif name == LUIGI_CFG_PATH.name:
                is_custom_luigi_cfg_file = True
    if not is_custom_logging_cfg_file:
        ssh(
            HPC_HEAD_NODE,
            f"cat > {workflows_path / LOGGING_CFG_PATH.name}",
            _in=Path(LOGGING_CFG_PATH).read_bytes(),
        )
    if not is_custom_luigi_cfg_file:
        ssh(
            HPC_HEAD_NODE,
            f"cat > {workflows_path / LUIGI_CFG_PATH.name}",
            _in=Path(LUIGI_CFG_PATH).read_bytes(),
        )


def _launch(buf, env, timestamp, module_name, task_name, cfg_name):
    """Launch the luigi task."""
    url = _reg_prov(buf, env, timestamp, module_name, task_name, cfg_name)
    workflows_path = WORKFLOWS_PATH / timestamp
    _sync_files(buf, workflows_path)
    env["PYTHONPATH"] = str(workflows_path)
    if cfg_name:
        env["LUIGI_CONFIG_PATH"] = str(workflows_path / cfg_name)

    cmd = 'bash -l -c "type singularity &> /dev/null || module load unstable singularityce; '
    cmd += "singularity run "
    cmd += f"-B {PATH_PREFIX}/{USER} "
    cmd += f"-B {DATA_PREFIX}:{DATA_PREFIX}:ro "
    cmd += f"--pwd {workflows_path} "
    cmd += f'{os.environ["HPC_SIF_PREFIX"]}/{BBP_WORKFLOW_SIF} '
    cmd += f"luigi --local-scheduler --logging-conf-file {workflows_path / LOGGING_CFG_PATH.name} "
    cmd += f'--module {module_name} {task_name}"'
    L.info("Launching: %s", cmd)

    Thread(target=_run_worker, args=(cmd, env)).start()

    return url


class VersionHandler(tornado.web.RequestHandler):
    """Handle version requests."""

    # pylint: disable=abstract-method

    def get(self, *_, **__):
        """Get version."""
        self.write(VERSION)


class HealthzHandler(tornado.web.RequestHandler):
    """Handle healthz requests."""

    # pylint: disable=abstract-method

    def get(self, *_, **__):
        """Get."""
        self.set_status(204)


class ApiLaunchHandler(tornado.web.RequestHandler):
    """Launch task through API."""

    # pylint: disable=abstract-method

    def get_current_user(self):
        return self.get_signed_cookie("user")

    def post(self, task):
        """Handle post."""
        L.info("API launch: %s", task)
        if self.current_user:
            env = {
                k: v
                for k, v in os.environ.items()
                if k.startswith("KC_") or k.startswith("HPC_") or k.startswith("NEXUS_")
            }
            env |= {"NEXUS_TOKEN": CRYPT.decrypt(self.current_user).decode()}
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

    def set_default_headers(self):
        """Provide set of default headers."""
        self.set_header("Access-Control-Allow-Origin", "https://openbluebrain.com")
        self.set_header("Access-Control-Allow-Methods", "OPTIONS, POST")
        self.set_header("Access-Control-Allow-Credentials", "true")
        self.set_header("Access-Control-Allow-Headers", "authorization")

    def options(self, _):
        """Handle options."""
        self.set_status(204)


class PostHandler(tornado.web.RequestHandler):
    """."""

    # pylint: disable=abstract-method

    async def get(self):
        """."""
        self.write((Path(__file__).parent / "templates" / "post.html").read_bytes())


async def main():
    """Start the workflow launcher."""
    app = tornado.web.Application(
        [
            ("/auth/", KeycloakAuthHandler),
            (r"/launch/([^/]+)/", ApiLaunchHandler),
            ("/post/", PostHandler),
            ("/healthz/", HealthzHandler),
        ],
        cookie_secret=Fernet.generate_key().decode(),
        template_path=str(Path(__file__).parent / "templates"),
    )
    app.listen(8100)

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
