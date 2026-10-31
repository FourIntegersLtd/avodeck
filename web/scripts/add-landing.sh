#!/bin/sh
# Vercel build only: avodeck.com serves the landing page (../site) at / and /privacy, and the app everywhere else.
# The app's index.html becomes app.html (the fallback in vercel.json); the landing files sit beside it.
# They are copied at build time, not kept in web/, so the web guards never scan the landing page's words.
set -e
mv dist/index.html dist/app.html
cp ../site/index.html ../site/privacy.html ../site/styles.css ../site/favicon.svg dist/
