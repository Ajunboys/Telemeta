#!/bin/sh

# paths
app='/srv/app'
manage=$app'/manage.py'
wsgi=$app'/wsgi.py'

# stating apps
pip install mysql django-environ redis

# waiting for other services
sh $app/deploy/wait.sh

# Starting celery worker with the --autoreload option will enable the worker to watch for file system changes
# This is an experimental feature intended for use in development only
# see http://celery.readthedocs.org/en/latest/userguide/workers.html#autoreloading
python $manage celery worker --autoreload -A celery_app
