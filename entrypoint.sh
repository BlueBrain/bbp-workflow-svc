#!/bin/bash
# Ensure that assigned uid has entry in /etc/passwd.
MYUID=$(id -u)
MYGID=$(id -G | cut -d ' ' -f 2)
UIDENTRY=$(getent passwd $MYUID)
if [ -z "$UIDENTRY" -a $MYUID -ge 10000 ] ; then
    # this is for local_server
    mkdir .ssh
    echo "$USER:x:$MYUID:$MYGID:$USER:/home/bbp-workflow:/bin/false" >> /etc/passwd
    echo -e "Host $HPC_HEAD_NODE\n    User=$USER\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null\n    ForwardAgent yes\n    LogLevel ERROR\n    SendEnv HPC_ENVIRONMENT HPC_HEAD_NODE HPC_PATH_PREFIX HPC_SIF_PREFIX HPC_DATA_PREFIX KC_HOST KC_SCR KC_REALM NEXUS_BASE NEXUS_ORG NEXUS_PROJ NEXUS_TOKEN NEXUS_WORKFLOW DEBUG DEBUG_KG PYTHONPATH LUIGI_CONFIG_PATH\n    ControlMaster auto\n    ControlPath ~/.ssh/%r@%h:%p" >> .ssh/config
else
    mkdir /root/.ssh
    echo -e "Host $HPC_HEAD_NODE\n    User=$USER\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null\n    ForwardAgent yes\n    LogLevel ERROR\n    SendEnv HPC_ENVIRONMENT HPC_HEAD_NODE HPC_PATH_PREFIX HPC_SIF_PREFIX HPC_DATA_PREFIX KC_HOST KC_SCR KC_REALM NEXUS_BASE NEXUS_ORG NEXUS_PROJ NEXUS_TOKEN NEXUS_WORKFLOW DEBUG DEBUG_KG PYTHONPATH LUIGI_CONFIG_PATH\n    ControlMaster auto\n    ControlPath ~/.ssh/%r@%h:%p" >> /root/.ssh/config
fi

eval $(ssh-agent)
ssh-add - <<< "$SSH_PRIVATE_KEY"

exec python -m bbp_workflow_svc.main
