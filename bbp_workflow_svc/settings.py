# SPDX-License-Identifier: Apache-2.0

"""Settings."""

import logging
import logging.config
import os
from pathlib import Path

from bbp_workflow_svc import __version__ as VERSION

DEBUG = os.getenv("DEBUG")
LOGGING_CFG = Path("logging.cfg")
if LOGGING_CFG.exists():
    logging.config.fileConfig(LOGGING_CFG, disable_existing_loggers=False)
logging.getLogger("entity_management").setLevel(
    logging.DEBUG if os.getenv("DEBUG_KG") else logging.INFO
)
L = logging.getLogger("bbp_workflow_svc")
L.setLevel(logging.DEBUG if DEBUG else logging.INFO)

ENVIRONMENT = os.getenv("HPC_ENVIRONMENT")

if ENVIRONMENT == "bbp":
    BBP_WORKFLOW_SIF = "py-bbp-workflow____py-bbp-workflow-dev.sif"
elif ENVIRONMENT == "aws":
    BBP_WORKFLOW_SIF = f'py-bbp-workflow__{".".join(VERSION.split(".")[:3])}-amd64.sif'
else:
    assert False, f"{ENVIRONMENT=} is not properly set!"
