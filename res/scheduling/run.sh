#!/bin/sh -e
. /etc/default/res/scheduling
if [ -z "$SETTINGS" ]; then
  echo "SETTINGS was not specified"
fi
python3 -m res.scheduling -vvv -c $SETTINGS $EXTRA
