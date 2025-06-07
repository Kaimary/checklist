FROM ubuntu:22.04

RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        git curl vim wget python3 \
        python3-dev python3-pip \
        libgl1 libglib2.0-0 && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    apt-get autoclean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple


RUN apt-get update && \
    apt-get install -y openssh-server sudo

WORKDIR /checklist

COPY . .
RUN pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && python -m spacy download en_core_web_sm