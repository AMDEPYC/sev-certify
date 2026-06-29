#!/bin/bash
set -euo pipefail

TIMEOUT=30
TTY="tty1"

# Print prompt to tty1
echo "Tests complete. System will reboot in $TIMEOUT seconds." > /dev/$TTY
echo "Press any key to cancel reboot and drop into root shell..." > /dev/$TTY

# Wait for keypress on tty1
if read -t "$TIMEOUT" -n 1 < /dev/$TTY; then
    echo "Key pressed — reboot cancelled." > /dev/$TTY

    # Stop existing getty so we can take over tty1
    systemctl stop getty@$TTY.service || true

    # Drop straight into root shell on tty1
    exec /sbin/agetty --autologin root --noclear $TTY $TERM
else
    echo "No key pressed. Rebooting now..." > /dev/$TTY
    systemctl reboot
fi
