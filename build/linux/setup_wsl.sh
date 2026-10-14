#!/usr/bin/env bash
set -euo pipefail

# setup_wsl.sh
# Installs required system packages for Astrometrics in WSL Ubuntu,
# including Node.js 24 (the LTS release that CI runs) from NodeSource.
# Ubuntu's own nodejs package lags behind and varies by release, so it is
# not used. Running the script again is safe; it skips Node.js when version
# NODE_MAJOR or newer is already installed.
# Run as root: sudo ./build/linux/setup_wsl.sh

# Keep in step with node-version in .github/workflows/frontend.yml and
# .github/workflows/lint.yml, and with @types/node in ui/mcp/package.json.
NODE_MAJOR=24

NODESOURCE_KEY_URL="https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
NODESOURCE_KEYRING="/etc/apt/keyrings/nodesource.gpg"
NODESOURCE_LIST="/etc/apt/sources.list.d/nodesource.list"
NODESOURCE_PIN="/etc/apt/preferences.d/nodesource"

# installed_node_major: prints the major version of the node on PATH, or 0.
installed_node_major() {
    if command -v node >/dev/null 2>&1; then
        node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0
    else
        echo 0
    fi
}

# install_nodejs: adds the NodeSource repository for NODE_MAJOR and installs
# nodejs from it. NodeSource's nodejs package already contains npm.
install_nodejs() {
    echo "Installing Node.js ${NODE_MAJOR} from NodeSource..."
    apt install -y ca-certificates curl gnupg

    # Ubuntu's separate npm package conflicts with NodeSource's nodejs.
    if dpkg -s npm >/dev/null 2>&1; then
        echo "Removing Ubuntu's npm package (NodeSource's nodejs includes npm)..."
        apt remove -y npm
    fi

    # Trust the NodeSource signing key for this one repository only.
    install -d -m 0755 /etc/apt/keyrings
    curl -fsSL "$NODESOURCE_KEY_URL" | gpg --dearmor --yes -o "$NODESOURCE_KEYRING"
    chmod 0644 "$NODESOURCE_KEYRING"
    echo "deb [signed-by=${NODESOURCE_KEYRING}] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
        > "$NODESOURCE_LIST"

    # Prefer NodeSource's nodejs over Ubuntu's, even if Ubuntu's version
    # number sorts higher.
    cat > "$NODESOURCE_PIN" <<EOF
Package: nodejs
Pin: origin deb.nodesource.com
Pin-Priority: 600
EOF

    apt update
    apt install -y nodejs
}

echo "=========================================="
echo "Astrometrics WSL Setup"
echo "=========================================="
echo ""

# Update package list
echo "Updating package list..."
apt update

# Install required packages
echo "Installing required packages..."
apt install -y \
    python3-pip \
    dos2unix \
    build-essential \
    python3-dev

current_node_major="$(installed_node_major)"
if [ "$current_node_major" -ge "$NODE_MAJOR" ]; then
    echo "Node.js $(node -v) is already installed; skipping."
else
    install_nodejs
fi

echo ""
echo "Node.js: $(node -v), npm: $(npm -v)"
echo "WSL setup complete!"
echo "You can now run the Astrometrics scripts."
