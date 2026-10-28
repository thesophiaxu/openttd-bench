#!/bin/sh
# This login shell predates the user's docker-group membership; `sg docker` applies it.
exec sg docker -c "$*"
