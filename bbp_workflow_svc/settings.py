# SPDX-License-Identifier: Apache-2.0

"""Settings."""

import logging
import logging.config
import os
from pathlib import Path

DEBUG = os.getenv("DEBUG")
LOGGING_CFG = Path("logging.cfg")
if LOGGING_CFG.exists():
    logging.config.fileConfig(LOGGING_CFG, disable_existing_loggers=False)
logging.getLogger("entity_management").setLevel(
    logging.DEBUG if os.getenv("DEBUG_KG") else logging.INFO
)
L = logging.getLogger("bbp_workflow_svc")
L.setLevel(logging.DEBUG if DEBUG else logging.INFO)
