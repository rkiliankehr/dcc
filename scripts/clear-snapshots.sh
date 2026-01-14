#!/bin/bash
# Remove all local APFS snapshots (Time Machine, Carbon Copy Cloner, etc.)

# Find the Data volume (usually disk3s5 but let's detect it)
DATA_DISK=$(diskutil list / | grep "APFS Volume Data" | awk '{print $NF}')

if [ -z "$DATA_DISK" ]; then
    echo "Could not find Data volume. Trying disk3s5..."
    DATA_DISK="disk3s5"
fi

echo "Using volume: $DATA_DISK"
echo ""

echo "Listing snapshots..."
snapshots=$(diskutil apfs listSnapshots "$DATA_DISK" 2>/dev/null | grep "Name:" | sed 's/.*Name:[[:space:]]*//')

if [ -z "$snapshots" ]; then
    echo "No snapshots found."
    exit 0
fi

count=$(echo "$snapshots" | wc -l | tr -d ' ')
echo "Found $count snapshots"
echo ""

echo "$snapshots" | while read -r snap; do
    echo "Deleting $snap..."
    sudo diskutil apfs deleteSnapshot "$DATA_DISK" -name "$snap" || echo "  Failed: $snap"
done

echo ""
echo "Done."
