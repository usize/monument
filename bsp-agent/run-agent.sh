#!/usr/bin/env bash

docker run --network host -e MONUMENT_NAMESPACE=$1 -e MONUMENT_AGENT_NAME=$2 MONUMENT_AGENT_SECRET=$3 bsp-agent:latest
