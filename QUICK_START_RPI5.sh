#!/bin/bash
# Quick start script for Raspberry Pi 5 with IMX219 cameras
# Usage: ./QUICK_START_RPI5.sh [backend]
# backend: picamera2 (default), libcamera, or v4l2

set -e

BACKEND=${1:-picamera2}

echo "=========================================="
echo "  CRAN Calibration - Raspberry Pi 5"
echo "=========================================="
echo ""

# Check if cameras are detected
echo "Checking cameras..."
if command -v rpicam-vid &> /dev/null; then
    echo "Camera list:"
    rpicam-vid --list-cameras 2>&1 | head -10
    echo ""
else
    echo "Warning: rpicam-vid not found. Install with: sudo apt install -y rpicam-apps"
fi

# Set environment variables based on backend
case "$BACKEND" in
    picamera2)
        echo "Using Picamera2 backend (recommended)"
        export CRAN_CAMERA_BACKEND=rpi5_picamera2
        
        # Check if picamera2 is available
        if ! python3 -c "import picamera2" 2>/dev/null; then
            echo ""
            echo "ERROR: Picamera2 not installed!"
            echo "Install with: sudo apt install -y python3-picamera2"
            exit 1
        fi
        ;;
    libcamera)
        echo "Using libcamera backend"
        export CRAN_CAMERA_BACKEND=rpi5_libcamera
        
        # Check if gstreamer libcamera is available
        if ! gst-inspect-1.0 libcamerasrc &>/dev/null; then
            echo ""
            echo "ERROR: GStreamer libcamera plugin not found!"
            echo "Install with: sudo apt install -y gstreamer1.0-libcamera"
            exit 1
        fi
        ;;
    v4l2)
        echo "Using V4L2 backend"
        export CRAN_CAMERA_BACKEND=rpi5_v4l2
        
        # Check if video devices exist
        if [ ! -e /dev/video0 ]; then
            echo ""
            echo "ERROR: /dev/video0 not found!"
            echo "Check camera connection and /boot/firmware/config.txt"
            exit 1
        fi
        ;;
    *)
        echo "ERROR: Unknown backend '$BACKEND'"
        echo "Usage: $0 [picamera2|libcamera|v4l2]"
        exit 1
        ;;
esac

# Camera device settings
export CRAN_BRIDGE_CAMERA_DEVICE="0"
export CRAN_HOOK_CAMERA_DEVICE="1"

# Optional: adjust resolution and framerate
# export CRAN_RPI5_CAMERA_WIDTH=1280
# export CRAN_RPI5_CAMERA_HEIGHT=720
# export CRAN_RPI5_CAMERA_FRAMERATE=10/1

echo "Camera settings:"
echo "  Backend: $CRAN_CAMERA_BACKEND"
echo "  Bridge camera: $CRAN_BRIDGE_CAMERA_DEVICE"
echo "  Hook camera: $CRAN_HOOK_CAMERA_DEVICE"
echo ""

# Check if we're in the right directory
if [ ! -f "main.py" ]; then
    echo "ERROR: main.py not found. Run this script from the Cran-New directory."
    exit 1
fi

# Check if dependencies are installed
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip install -r requirements.txt
fi

echo "Starting CRAN Calibration service..."
echo "Access at: http://$(hostname -I | awk '{print $1}'):8000"
echo "Login: admin / admin"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Start the application
uvicorn main:app --host 0.0.0.0 --port 8000
