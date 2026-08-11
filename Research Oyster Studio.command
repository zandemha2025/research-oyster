#!/bin/bash
# Double-click this file to open Research Oyster Studio.
# (macOS: the first time, right-click → Open to get past Gatekeeper.)
# It runs the self-healing ./studio launcher, which sets up anything missing,
# starts the database, and opens the Studio in your browser.
cd "$(dirname "$0")"
exec ./studio
