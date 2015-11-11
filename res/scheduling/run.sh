#!/bin/sh -e
. /etc/default/res/scheduling
if [ -z "$SETTINGS" ]; then
  echo "SETTINGS was not specified"
fi
if [ -z "$PROCESSES" ]; then
  PROCESSES=1
fi
python3 -m res.scheduling -vvv -c $SETTINGS -p $PROCESSES $EXTRA
