#!/usr/bin/env python3
"""Add BSC wallet to Gateway using W_PK from .env and persist it.

This script reads W_PK and GATEWAY_URL from the repository .env (or environment),
posts it to the Gateway /wallet/add endpoint to register and persist a BSC wallet,
backs up the gateway wallet folder, and prints the resulting wallet address and
verification about the persisted wallet file. It NEVER prints the private key.
"""
import json
import os
import sys
import time
from pathlib import Path
from shutil import copytree

HERE = Path(__file__).resolve().parents[1]
ENV_PATH = HERE / ".env"
