#! /bin/sh -e
set -x

ruff $@ --fix
black $@
isort $@ --profile black