FROM langchain/langgraph-api:3.13

RUN apt update && apt upgrade -y
RUN apt install -y build-essential wget curl vim lsb-release gnupg2

ADD . /deps/next-role

RUN PYTHONDONTWRITEBYTECODE=1 pip install --no-cache-dir -c /api/constraints.txt -e /deps/*

WORKDIR /deps/next-role
