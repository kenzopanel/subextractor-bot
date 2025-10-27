#!/bin/bash

aria2c --conf-path=/app/aria2.conf
sleep 1

python3 main.py