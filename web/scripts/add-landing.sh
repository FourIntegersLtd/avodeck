#!/bin/sh
# Vercel build only: avodeck.com serves the landing page (public/) at / and the legal pages,
# and the app everywhere else.  Vite copies public/ into dist/ automatically, so the landing
# files are already there.  This script renames the app's index.html out of the way and puts
# the landing page's index in its place.
set -e
mv dist/index.html dist/app.html
mv dist/index-landing.html dist/index.html
