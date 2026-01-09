"""Simulate CLMM position over the last 24 hours using price history.

This is an approximation using constant-product math per timestamp.
It fetches pool info from Gateway to find the base token contract address and
then uses CoinGecko's 'binance-smart-chain' contract endpoint to get 24h prices.

Outputs a simple CSV-like summary to stdout and writes a log to tmp/sim_history.log.
"""
import asyncio
import os
import sys
import time
import math
import json
from decimal import Decimal
import importlib.util
from pathlib import Path

import aiohttp
