#!/bin/bash
# Push next 8 days of BR100 workouts to Intervals.icu (syncs to Coros)
cd /home/michaelpawlus/projects/workout-app/backend
set -a
source .env
set +a
/usr/bin/python3 cli.py ultra icu-push --upcoming 8
