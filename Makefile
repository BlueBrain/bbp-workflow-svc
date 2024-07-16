.PHONY: help python_build docker_build_latest local_server

IMAGE_NAME?=bbp-workflow-svc


define HELPTEXT
Makefile usage
 Targets:
    python_build        Build, test and package python.
    docker_build_latest Build backend local docker image with the latest tag.
endef
export HELPTEXT

help:
	@echo "$$HELPTEXT"

python_build:
	tox -e py3
	pipx run build --sdist

docker_build_latest: python_build
	docker build -t $(IMAGE_NAME):latest .

local_server:
	docker run -it --rm --user $$(id -u) -p 8100:8100 \
		-v $$(pwd)/bbp_workflow_svc:/usr/local/lib/python3.11/site-packages/bbp_workflow_svc \
		-e DEBUG=True \
		-e USER=$$(whoami) \
		-e REDIRECT_URI=$$REDIRECT_URI \
		-e KC_HOST=$$KC_HOST \
		-e KC_REALM=$$KC_REALM \
		-e KC_SCR=$$KC_SCR \
		-e HPC_HEAD_NODE=$$HPC_HEAD_NODE \
		-e HPC_ENVIRONMENT=$$HPC_ENVIRONMENT \
		-e HPC_PATH_PREFIX=$$HPC_PATH_PREFIX \
		-e HPC_DATA_PREFIX=$$HPC_DATA_PREFIX \
		-e HPC_SIF_PREFIX=$$HPC_SIF_PREFIX \
		-e SSH_PRIVATE_KEY="$$SSH_PRIVATE_KEY" \
		-e NEXUS_BASE=$$NEXUS_BASE \
		$(IMAGE_NAME)
