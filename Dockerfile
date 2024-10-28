FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -q -y --no-install-recommends curl rsync ssh

COPY requirements.txt /tmp

RUN apt-get install -q -y --no-install-recommends build-essential && \
    pip install --no-cache-dir --upgrade setuptools pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    apt-get purge -q -y --auto-remove build-essential

EXPOSE 8100

WORKDIR /home/bbp-workflow

COPY luigi.cfg /etc/luigi/luigi.cfg
COPY logging.cfg ./

ENV HOME=/home/bbp-workflow

COPY dist/* dist/
RUN pip install --no-cache-dir $(ls -t $PWD/dist/*.* | head -n 1)

ENTRYPOINT ["python", "-m", "bbp_workflow_svc.main"]
