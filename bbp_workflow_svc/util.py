# SPDX-License-Identifier: Apache-2.0

"""Utils."""

import os

# import re
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit


def params_from_cfg(task_cls, cfg_section, default_section):
    """Lookup params from cfg with defaults."""
    params = {}
    for param_name_, _ in task_cls.get_params():
        param_name = param_name_.replace("_", "-")  # correct for underscore
        if param_name in cfg_section:
            params[param_name_] = cfg_section[param_name]
        elif param_name_ in cfg_section:  # try name with underscore anyway
            params[param_name_] = cfg_section[param_name_]
        elif param_name in default_section:
            params[param_name_] = default_section[param_name]
        elif param_name_ in default_section:
            params[param_name_] = default_section[param_name_]
    return task_cls(**params)


def _ignore_slurm_config_key(key):
    """List of slurm config keys which should be ignored."""
    return key in ["env"]


def map_slurm_params(conf, skip=None):
    """Map params to strings.

    Args:
        conf (SlurmCfg): Slurm configuration.
    """
    rename = {"job_output": "output", "job_array": "array"}
    params = []
    if conf is None:
        return params
    for key, _param in conf.get_params():
        if skip and key in skip:
            continue
        val = getattr(conf, key)
        if val:
            key = rename.get(key, key)
            key = key.replace("_", "-")
            # if isinstance(param, BoolParameter):
            #     params.append(f'--{key}')
            # else:
            #     params.append(f'--{key}={val}')

    return params


def to_sbatch_params(conf):
    """Sbatch param helper.

    Converts {key: value} to string #SBATCH --key=val if val is not None
    also replace _ to - in key.
    """
    return "\n".join([f"#SBATCH {param}" for param in map_slurm_params(conf)])


def to_srun_params(conf, job_id=None):
    """Srun params helper.

    Converts {key: value} to string --key=val if val is not None
    also replace _ to - in key.
    """
    params = map_slurm_params(conf, skip=["wait"])
    if job_id:
        params.append(f"--jobid={job_id}")
    return " ".join(params)


def kg_env_exports(kg_env=None):
    """Take nexus relevant env var and produce export statements."""
    ret = []
    if kg_env:
        for k, v_v in kg_env.items():
            ret.append(f'export {k}="{v_v}"')
    else:
        for env_var in (
            "NEXUS_TOKEN",
            "NEXUS_WORKFLOW",
            "NEXUS_BASE",
            "NEXUS_USERINFO",
            "NEXUS_ORG",
            "NEXUS_PROJ",
            "NEXUS_NO_PROV",
            "NEXUS_DRY_RUN",
            "KC_SCR",
        ):
            if env_var in os.environ:
                ret.append(f'export {env_var}="{os.environ[env_var]}"')
    return ret


# def cmd_join(cmd_args):
#     '''Take space/new_line separated cmd args and produce one liner.'''
#     if cmd_args:
#         return ' '.join([var.strip() for var in re.split(r' |\n', cmd_args) if var.strip()])
#     else:
#         return ''


# def env_exports(env):
#     '''Take comma separated env vars and produce array of export statements.'''
#     if env:
#         return [f'export {var.strip()}' for var in re.split(r',|\n', env) if var.strip()]
#     else:
#         return []


def to_env_commands(env_cfg, kg_env=None, python_path=None):
    """Make shell commands out of EnvCfg.

    Args:
        env_cfg (EnvCfg): Environment configuration params.
    Returns:
        list: [modulepath export, module load command, env exports].
    """
    result = []
    if env_cfg is None:
        return result

    if env_cfg.modules:
        if env_cfg.module_path:
            result.append(f"export MODULEPATH={env_cfg.module_path}:$MODULEPATH")

        if env_cfg.module_archive:
            result.append(f"module load {env_cfg.module_archive}")
        else:
            result.append("module load unstable")

        result.append(f"module load {env_cfg.modules}")

    result.extend(kg_env_exports(kg_env))

    # if env_cfg.enable_internet:
    #     result.append(f'export https_proxy={HTTPS_PROXY}')

    if env_cfg.virtual_env:
        result.append(f"source {env_cfg.virtual_env}/bin/activate")

    if python_path:
        result.append(f'export PYTHONPATH="{python_path}":$PYTHONPATH')

    # if env_cfg.env:
    #     result.extend(env_exports(env_cfg.env))

    return result


def make_web_link(base, org, proj, resource_id):
    """Make link to nexus web from the resource id."""
    if base and "staging" in str(base):
        prefix = "https://staging.nise.bbp.epfl.ch/nexus"
    else:
        prefix = "https://bbp.epfl.ch/nexus/web"

    if org is None:
        org = "bbp"

    resource_id = quote(resource_id, safe="")

    return f"{prefix}/{org}/{proj}/resources/{resource_id}"


def _fix_bbp_link(url):
    """Fix url by adding ``.bbp.epfl.ch`` suffix if not present."""
    split = urlsplit(url)
    try:
        ip_address(split.hostname)
        # got ip address => pass it as is
        return url
    except ValueError:
        # got host name => add suffix
        suffix = ""
        if ".bbp.epfl.ch" not in split.hostname:
            suffix += ".bbp.epfl.ch"
        if split.port:
            suffix += f":{split.port}"

        return urlunsplit(split._replace(netloc=split.hostname + suffix))


def metadata_path(output_path, chdir=None):
    """Return (path, name) to metadata file based on task output path and cwd."""
    output_path = Path(output_path)
    if not chdir or output_path == Path(chdir):
        return (str(output_path.parent), str(output_path.name))

    try:
        relative = str(output_path.relative_to(chdir))
        return (chdir, relative.replace("/", "--"))
    except ValueError:
        return (str(output_path.parent), str(output_path.name))
