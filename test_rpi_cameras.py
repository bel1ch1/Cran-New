#!/usr/bin/env python3
"""
Diagnostic script to test camera access on Raspberry Pi 5.
Tests all available methods: Picamera2, libcamera, and V4L2.
"""
import sys
import subprocess

def print_section(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def test_picamera2():
    print_section("Testing Picamera2")
    try:
        from picamera2 import Picamera2
        print("✓ Picamera2 library is available")
        
        try:
            cameras = Picamera2.global_camera_info()
            print(f"✓ Found {len(cameras)} camera(s)")
            for i, cam in enumerate(cameras):
                print(f"  Camera {i}: {cam}")
            
            # Try to open camera 0
            print("\nTrying to open camera 0...")
            picam2 = Picamera2(0)
            config = picam2.create_preview_configuration(main={"size": (640, 480)})
            picam2.configure(config)
            picam2.start()
            print("✓ Camera 0 opened successfully")
            
            # Capture a test frame
            frame = picam2.capture_array()
            print(f"✓ Captured frame: shape={frame.shape}, dtype={frame.dtype}")
            
            picam2.stop()
            picam2.close()
            print("✓ Camera closed successfully")
            return True
        except Exception as e:
            print(f"✗ Failed to use Picamera2: {e}")
            return False
    except ImportError:
        print("✗ Picamera2 not installed")
        print("  Install with: sudo apt install -y python3-picamera2")
        return False

def test_libcamera_tools():
    print_section("Testing libcamera tools")
    
    # Test rpicam-vid
    try:
        result = subprocess.run(
            ["rpicam-vid", "--list-cameras"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print("✓ rpicam-vid is available")
            print("\nCamera list:")
            print(result.stdout)
        else:
            print("✗ rpicam-vid failed")
    except FileNotFoundError:
        print("✗ rpicam-vid not found")
    except Exception as e:
        print(f"✗ Error running rpicam-vid: {e}")
    
    # Test libcamera-hello
    try:
        result = subprocess.run(
            ["libcamera-hello", "--list-cameras"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print("\n✓ libcamera-hello is available")
        else:
            print("\n✗ libcamera-hello failed")
    except FileNotFoundError:
        print("\n✗ libcamera-hello not found")
    except Exception as e:
        print(f"\n✗ Error running libcamera-hello: {e}")

def test_opencv_gstreamer():
    print_section("Testing OpenCV with GStreamer")
    try:
        import cv2
        print(f"✓ OpenCV version: {cv2.__version__}")
        
        # Check GStreamer support
        build_info = cv2.getBuildInformation()
        if "GStreamer" in build_info:
            print("✓ OpenCV built with GStreamer support")
        else:
            print("✗ OpenCV not built with GStreamer support")
            return False
        
        # Test libcamerasrc pipeline
        print("\nTrying libcamerasrc pipeline...")
        pipeline = (
            "libcamerasrc ! "
            "video/x-raw,width=640,height=480,framerate=10/1,format=NV12 ! "
            "queue ! videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
        )
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                print(f"✓ libcamerasrc works! Frame shape: {frame.shape}")
                cap.release()
                return True
            else:
                print("✗ Could not read frame from libcamerasrc")
                cap.release()
        else:
            print("✗ Could not open libcamerasrc pipeline")
        
        # Test v4l2src pipeline
        print("\nTrying v4l2src pipeline...")
        pipeline = (
            "v4l2src device=/dev/video0 ! "
            "video/x-raw,width=640,height=480 ! "
            "videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
        )
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                print(f"✓ v4l2src works! Frame shape: {frame.shape}")
                cap.release()
                return True
            else:
                print("✗ Could not read frame from v4l2src")
                cap.release()
        else:
            print("✗ Could not open v4l2src pipeline")
        
        return False
    except ImportError:
        print("✗ OpenCV not installed")
        return False
    except Exception as e:
        print(f"✗ Error testing OpenCV: {e}")
        return False

def test_v4l2_devices():
    print_section("Testing V4L2 devices")
    import os
    video_devices = [f"/dev/video{i}" for i in range(10) if os.path.exists(f"/dev/video{i}")]
    
    if video_devices:
        print(f"✓ Found {len(video_devices)} video device(s):")
        for dev in video_devices:
            print(f"  {dev}")
        
        # Try to get device info
        try:
            for dev in video_devices:
                result = subprocess.run(
                    ["v4l2-ctl", "--device", dev, "--all"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    print(f"\n{dev} info:")
                    # Print first few lines
                    lines = result.stdout.split('\n')[:5]
                    for line in lines:
                        print(f"  {line}")
        except FileNotFoundError:
            print("\n  (v4l2-ctl not found, install with: sudo apt install v4l-utils)")
        except Exception as e:
            print(f"\n  Error getting device info: {e}")
    else:
        print("✗ No /dev/video* devices found")

def main():
    print("\n" + "=" * 60)
    print("  Raspberry Pi 5 Camera Diagnostic Tool")
    print("=" * 60)
    
    test_libcamera_tools()
    test_v4l2_devices()
    picamera2_ok = test_picamera2()
    opencv_ok = test_opencv_gstreamer()
    
    print_section("Summary & Recommendations")
    
    if picamera2_ok:
        print("✓ RECOMMENDED: Use Picamera2 backend")
        print("  Set: CRAN_CAMERA_BACKEND=rpi5_picamera2")
    elif opencv_ok:
        print("✓ Use GStreamer backend (libcamera or v4l2)")
        print("  Set: CRAN_CAMERA_BACKEND=rpi5_libcamera")
        print("  or:  CRAN_CAMERA_BACKEND=rpi5_v4l2")
    else:
        print("✗ No working camera backend found!")
        print("\nTroubleshooting steps:")
        print("1. Check /boot/firmware/config.txt:")
        print("   camera_auto_detect=0")
        print("   dtoverlay=imx219,cam0")
        print("   dtoverlay=imx219")
        print("2. Reboot after config changes")
        print("3. Install Picamera2: sudo apt install -y python3-picamera2")
        print("4. Install GStreamer: sudo apt install -y gstreamer1.0-libcamera")
    
    print("\n")

if __name__ == "__main__":
    main()
