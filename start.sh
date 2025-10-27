#!/bin/bash

# Create required directories
mkdir -p /tmp/aria2

# Start aria2c with modified settings
aria2c --conf-path=/app/aria2.conf \
        --rpc-listen-all=true \
        --rpc-allow-origin-all=true \
        --rpc-secret="" \
        --continue=true

# Wait for aria2c to start
sleep 2

# Start the bot
python3 main.py