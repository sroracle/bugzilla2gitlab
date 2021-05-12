#!/usr/bin/env python3
import json
import sys

data = json.load(sys.stdin)
ids = "\n".join([str(i["id"]) for i in data["bugs"]])
print(ids)
