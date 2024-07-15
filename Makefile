.PHONY: help python_build build_latest push_latest local_server

REGISTRY?=$(ACCOUNT_ID).dkr.ecr.$(REGION).amazonaws.com
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
	pipx run build --sdist

build_latest: python_build
	docker build -t $(IMAGE_NAME):latest .

push_latest: build_latest
	aws ecr get-login-password --region $(REGION) | docker login --username AWS --password-stdin $(REGISTRY)
	docker tag $(IMAGE_NAME):latest $(REGISTRY)/$(IMAGE_NAME):latest
	docker push $(REGISTRY)/$(IMAGE_NAME):latest
	docker logout $(REGISTRY)

local_server:
	docker run -it --rm --user $$(id -u) -p 8100:8100 \
		-e DEBUG=True \
		-e USER=$$(whoami) \
		-e REDIRECT_URI=$$REDIRECT_URI \
		-e KC_HOST=$$KC_HOST \
		-e KC_REALM=$$KC_REALM \
		-e KC_SCR=$$KC_SCR \
		-e KC_SUB=$$KC_SUB \
		-e HPC_HEAD_NODE=$$HPC_HEAD_NODE \
		-e HPC_ENVIRONMENT=$$HPC_ENVIRONMENT \
		-e HPC_PATH_PREFIX=$$HPC_PATH_PREFIX \
		-e HPC_DATA_PREFIX=$$HPC_DATA_PREFIX \
		-e HPC_SIF_PREFIX=$$HPC_SIF_PREFIX \
		-e NEXUS_BASE=$$NEXUS_BASE \
		-e SESSION_ID=$$SESSION_ID \
		$(IMAGE_NAME)
