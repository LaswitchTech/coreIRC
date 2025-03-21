#!/bin/bash

# Function to print messages with a timestamp
log() {
    echo "$(date +'%Y-%m-%d %H:%M:%S') - $1"
}

# Function to detect the operating system
detect_os() {
    case "$(uname -s)" in
        Darwin)
            echo "macos"
            ;;
        Linux)
            echo "linux"
            ;;
        *)
            echo "unsupported"
            ;;
    esac
}

# Function to detect the root folder of the project based on the location of the script (src/build.sh)
detect_root() {
    local ROOT_DIR
    ROOT_DIR=$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)")
    echo "$ROOT_DIR"
}

# Set some directories
CURRENT_DIR=$(pwd)
ROOT_DIR=$(detect_root)

# Set the name of the application
NAME="coreIRC"

# Determine the operating system
OS=$(detect_os)
if [ "$OS" == "unsupported" ]; then
    log "Unsupported operating system. Exiting."
    exit 1
fi

# Change to the root directory of the project
cd $ROOT_DIR

# Check if the Python virtual environment is already set up
if [ ! -f "env/bin/python" ] || [ ! -f "env/bin/python3" ] || [ ! -f "env/bin/pip" ]; then
    log "Python virtual environment not found. Creating a new one..."
    python3 -m venv env --system-site-packages
else
    log "Python virtual environment found."
fi

# Activate the environment
source env/bin/activate

# Ensure that pip is updated
log "Updating pip..."
python -m pip install --upgrade pip

# Ensure the necessary packages are installed
log "Installing required packages..."
pip install pyinstaller
pip install mysql-connector-python
pip install bcrypt
pip install pyfiglet
pip install prompt_toolkit
exit
pip install sip importlib psutil chardet
pip install PySide6-Addons
pip install pyqt5 --config-settings --confirm-license= --verbose

# Check if the .spec file exists
SPEC_FILE="$NAME.spec"
ICON_FILE="src/icons/icon.icns"

# Cleanup: Remove the leftover dist/$NAME directory on macOS
log "Cleaning up..."
if [ -d "dist/$NAME" ]; then
    rm -rf "dist/$NAME"
fi
if [ -f "$SPEC_FILE" ]; then
    rm -f "$SPEC_FILE"
fi

log ".spec file not found. Generating a new one with Configurator..."
if [ "$OS" == "macos" ]; then
    pyinstaller --windowed --name "$NAME" src/server.py
    pyinstaller --windowed --name "$NAME" src/client.py
elif [ "$OS" == "linux" ]; then
    pyinstaller --onefile --name "$NAME" src/server.py
    pyinstaller --onefile --name "$NAME" src/client.py
fi

# Ensure the spec file now exists
if [ ! -f "$SPEC_FILE" ]; then
    log "Failed to create .spec file. Exiting."
    exit 1
fi

log "Generated .spec file: $SPEC_FILE"

# Update the .spec file to include the custom icon, data files, and hidden imports
log "Updating the .spec file to include the custom icon, data files, and hidden imports..."
if [ "$OS" == "macos" ]; then
    sed -i '' "s|icon=None|icon='$ICON_FILE'|g" $SPEC_FILE
    sed -i '' "/Analysis/s/(.*)/\0, hiddenimports=['PyQt5.QtSvg']/" $SPEC_FILE
    sed -i '' "/a.datas +=/a \\
        datas=[('src/styles', 'styles'), ('src/icons', 'icons'), ('src/img', 'img'), ('src/lib', 'lib')],
    " $SPEC_FILE
elif [ "$OS" == "linux" ]; then
    sed -i "s|icon=None|icon='$ICON_FILE'|g" $SPEC_FILE
    sed -i "/Analysis/s/(.*)/\0, hiddenimports=['PyQt5.QtSvg']/" $SPEC_FILE
    sed -i "/a.datas +=/a \\
        datas=[('src/styles', 'styles'), ('src/icons', 'icons'), ('src/img', 'img'), ('src/lib', 'lib')],
    " $SPEC_FILE
fi

# Build the project with PyInstaller using the updated .spec file
log "Building the project with PyInstaller..."
pyinstaller --noconfirm $SPEC_FILE

# Copy resources into the appropriate location
if [ "$OS" == "macos" ]; then
    APP_BUNDLE="dist/$NAME.app/Contents/Resources"

    log "Copying resources into the app bundle..."
    mkdir -p "$APP_BUNDLE/styles"
    mkdir -p "$APP_BUNDLE/img"
    mkdir -p "$APP_BUNDLE/icons"
    mkdir -p "$APP_BUNDLE/lib"

    cp -R src/styles/* "$APP_BUNDLE/styles/"
    cp -R src/img/* "$APP_BUNDLE/img/"
    cp -R src/icons/* "$APP_BUNDLE/icons/"
    cp -R src/lib/* "$APP_BUNDLE/lib/"

else
    log "Linux build does not require copying resources to a separate directory, as it is a single-file executable."

    # Note: If you need to bundle resources within the executable, adjust the PyInstaller options to include those resources
fi

# Create a directory to store the final output based on the OS
FINAL_DIR="dist/$OS"
if [ -d "$FINAL_DIR" ]; then
    rm -rf "$FINAL_DIR"
fi
mkdir -p "$FINAL_DIR"

# Move the built application or executable to the appropriate directory
if [ "$OS" == "macos" ]; then
    log "Moving the .app bundle to the $FINAL_DIR directory..."
    mv "dist/$NAME.app" "$FINAL_DIR/"

    # Create a DMG image
    log "Creating a DMG image for macOS..."
    DMG_NAME="$FINAL_DIR/$NAME.dmg"
    hdiutil create "$DMG_NAME" -volname "$NAME" -srcfolder "$FINAL_DIR/$NAME.app" -ov -format UDZO

    log "DMG image created at $DMG_NAME"
else
    log "Moving the executable to the $FINAL_DIR directory..."
    mv "dist/$NAME" "$FINAL_DIR/"
fi

# Cleanup: Remove the leftover dist/$NAME directory on macOS
if [ -d "dist/$NAME" ]; then
    log "Cleaning up the dist directory..."
    rm -rf "dist/$NAME"
fi

log "Build completed successfully."

# Deactivate the virtual environment
deactivate
