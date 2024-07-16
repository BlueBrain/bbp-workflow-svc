FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN ln -sf /usr/share/zoneinfo/Europe/Zurich /etc/localtime

RUN apt-get update && apt-get install -q -y --no-install-recommends curl rsync ssh jq htop

COPY requirements.txt /tmp

RUN apt-get install -q -y --no-install-recommends build-essential && \
    pip install --no-cache-dir --upgrade setuptools pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    apt-get purge -q -y --auto-remove build-essential

# enable entrypoint add user with uid:gid
RUN chmod a+w /etc/passwd

EXPOSE 8100

WORKDIR /opt/bbp-workflow

COPY entrypoint.sh ./

WORKDIR /home/bbp-workflow

COPY luigi.cfg ./
COPY logging.cfg ./

ENV HOME=/home/bbp-workflow

COPY dist/* dist/
RUN pip install --no-cache-dir $(ls -t $PWD/dist/*.* | head -n 1)

ENTRYPOINT ["/opt/bbp-workflow/entrypoint.sh"]
