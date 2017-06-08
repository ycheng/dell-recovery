#!/bin/sh
set -e
dpkg-buildpackage -tc -us -uc
