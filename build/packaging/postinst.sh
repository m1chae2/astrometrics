#!/usr/bin/env bash
set -euo pipefail

# postinst: configure astrometrics after package install
# Responsibilities:
# - ensure /opt/astrometrics permissions
# - create a python virtualenv under /opt/astrometrics/venv
# - install backend wheel into the venv
# - create a wrapper /usr/local/bin/astrometrics-backend that runs the start script inside the venv
# - if a frontend .deb was staged at /opt/astrometrics/frontend.deb, install it via dpkg -i and fix deps

echo "[postinst] Configuring Astrometrics backend..."

ASTRO_DIR="/opt/astrometrics"
VENV_DIR="$ASTRO_DIR/venv"

if [ ! -d "$ASTRO_DIR" ]; then
  echo "[postinst] ERROR: $ASTRO_DIR does not exist! Package installation may have failed."
  echo "[postinst] Attempting to create directory..."
  mkdir -p "$ASTRO_DIR"
fi

echo "[postinst] Contents of $ASTRO_DIR:"
ls -la "$ASTRO_DIR" || echo "[postinst] Directory is empty or doesn't exist"

echo "[postinst] Setting permissions for $ASTRO_DIR"
chmod -R a+rX "$ASTRO_DIR" || true

# Ensure python3 is available
if ! command -v python3 >/dev/null 2>&1; then
  echo "[postinst] python3 not found. Attempting to install python3..."
  apt-get update -y || true
  apt-get install -y python3 || true
fi

# Ensure pip is available
if ! command -v pip3 >/dev/null 2>&1; then
  echo "[postinst] pip3 not found. Installing python3-pip..."
  apt-get update -y || true
  apt-get install -y python3-pip || true
fi

echo "[postinst] Creating or updating virtualenv at $VENV_DIR"
python3 -m venv "$VENV_DIR" || true
"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel || true

# Install wheel(s) from /opt/astrometrics into the venv
WHEEL_GLOB=("$ASTRO_DIR"/astrometrics-*.whl)
if [ -e "${WHEEL_GLOB[0]}" ]; then
  for W in "${WHEEL_GLOB[@]}"; do
    if [ -f "$W" ]; then
      echo "[postinst] Installing wheel $W into venv"
      "$VENV_DIR/bin/pip" install --upgrade "$W" || true
    fi
  done
else
  echo "[postinst] No backend wheel found in $ASTRO_DIR; skipping wheel install"
fi

# Prepare a wrapper script that runs the backend start script inside the venv
WRAPPER="/usr/local/bin/astrometrics-backend"
START_SCRIPT_CANDIDATES=("$ASTRO_DIR/start_astrometrics.sh" "$ASTRO_DIR/astrometrics/start_astrometrics.sh")
SELECTED_START=""
for s in "${START_SCRIPT_CANDIDATES[@]}"; do
  if [ -x "$s" ]; then
    SELECTED_START="$s"
    break
  fi
done

# Create wrapper - either using start script or Python module directly
if [ -n "$SELECTED_START" ]; then
  echo "[postinst] Using start script: $SELECTED_START"
  cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
VENV="$VENV_DIR"
export PATH="\$VENV/bin:\$PATH"
exec "$SELECTED_START" "\$@"
EOF
  chmod +x "$WRAPPER"
  echo "[postinst] Created wrapper $WRAPPER"
elif [ -d "$VENV_DIR" ]; then
  # No start script found, but venv exists - try to invoke Python module directly
  echo "[postinst] No start script found; creating wrapper to invoke Python module directly"
  cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
VENV="$VENV_DIR"
export PATH="\$VENV/bin:\$PATH"
cd "$ASTRO_DIR"
exec "\$VENV/bin/python3" -m backend.main_backend "\$@"
EOF
  chmod +x "$WRAPPER"
  echo "[postinst] Created Python module wrapper at $WRAPPER"
else
  echo "[postinst] No start script or venv found; wrapper not created"
fi

# Ensure chrome-sandbox has correct permissions (root:root, 4755)
# This is usually handled by the package manager, but we fix it here as a safeguard.
CS_CANDIDATES=(
  "/usr/lib/astrometrics/chrome-sandbox"
  "/opt/astrometrics/chrome-sandbox"
  "/usr/bin/chrome-sandbox"
)
for cs in "${CS_CANDIDATES[@]}"; do
  if [ -f "$cs" ]; then
    echo "[postinst] Found chrome-sandbox at $cs, ensuring root:root 4755"
    chown root:root "$cs" || true
    chmod 4755 "$cs" || true
  fi
done

# Decide which user should run the service: prefer the invoking sudo user if available
RUN_USER=""
if [ -n "${SUDO_USER-}" ] && [ "${SUDO_USER-}" != "root" ]; then
  RUN_USER="$SUDO_USER"
  echo "[postinst] Installer user detected via SUDO_USER: $RUN_USER"
else
  # create a system user 'astrometrics' if it doesn't exist
  if id -u astrometrics >/dev/null 2>&1; then
    RUN_USER="astrometrics"
  else
    echo "[postinst] Creating system user 'astrometrics'"
    useradd --system --no-create-home --shell /usr/sbin/nologin astrometrics || true
    RUN_USER="astrometrics"
  fi
fi

# chown files to RUN_USER so the service can access them
echo "[postinst] Setting ownership of $ASTRO_DIR to $RUN_USER"
chown -R "$RUN_USER":"$RUN_USER" "$ASTRO_DIR" || true



# Install systemd unit and enable/start the service (if systemd is present and enabled)
SYSTEMD_UNIT_SRC="/opt/astrometrics/astrometrics.service"
SYSTEMD_UNIT_DEST="/lib/systemd/system/astrometrics-backend.service"
ENABLE_MARKER="$ASTRO_DIR/enable-service"
if [ -f "$SYSTEMD_UNIT_SRC" ]; then
  echo "[postinst] Installing systemd unit from $SYSTEMD_UNIT_SRC"
  # Inject RUN_USER into the unit file: replace User= line or add after [Service]
  TMP_UNIT="/tmp/astrometrics-backend.service"
  sed -e "s/^User=.*$/User=$RUN_USER/" "$SYSTEMD_UNIT_SRC" > "$TMP_UNIT" || true
  # If no User= line was present, insert it after [Service]
  if ! grep -q "^User=" "$TMP_UNIT" >/dev/null 2>&1; then
    awk -v userline="User=$RUN_USER" '
    BEGIN{inserted=0}
    {print $0}
    $0=="[Service]"{getline; print; print userline; inserted=1; next}
    END{if(!inserted) print userline}' "$SYSTEMD_UNIT_SRC" > "$TMP_UNIT" || true
  fi
  cp -f "$TMP_UNIT" "$SYSTEMD_UNIT_DEST" || true
  rm -f "$TMP_UNIT" || true

  if [ -f "$ENABLE_MARKER" ]; then
    if command -v systemctl >/dev/null 2>&1; then
      systemctl daemon-reload || true
      systemctl enable --now astrometrics-backend.service || true
      echo "[postinst] systemd service enabled and started as $RUN_USER"
    else
      echo "[postinst] systemctl not available; skipping service enable/start"
    fi
  else
    echo "[postinst] Service installation skipped (no enable marker found). To enable service, create $ENABLE_MARKER before installing or rebuild package with --enable-service."
  fi
else
  echo "[postinst] No staged systemd unit at $SYSTEMD_UNIT_SRC; skipping service setup"
fi

# If a frontend .deb was staged into /opt/astrometrics/frontend.deb (by the package), install it
FRONTEND_DEB="$ASTRO_DIR/frontend.deb"
if [ -f "$FRONTEND_DEB" ]; then
  echo "[postinst] Installing staged frontend deb: $FRONTEND_DEB"
  dpkg -i "$FRONTEND_DEB" || true
  apt-get update -y || true
  apt-get install -f -y || true
fi

echo "[postinst] done"

exit 0
