"""
Comprehensive verification script for all Docker SDK steps (3.1-3.5)

This script runs all verification steps in sequence and provides a summary report.
"""

import docker
import io
import os
import tarfile
import sys

def step_3_1_docker_connection():
    """Step 3.1 - Test Docker connection"""
    print("=" * 60)
    print("STEP 3.1 - Docker Connection")
    print("=" * 60)
    
    try:
        client = docker.from_env()
        result = client.ping()
        print(f"✅ Docker ping: {result}")
        return True, client
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False, None


def step_3_2_create_container(client):
    """Step 3.2 - Create Container"""
    print("\n" + "=" * 60)
    print("STEP 3.2 - Create Container")
    print("=" * 60)
    
    try:
        # Pull image
        print("📥 Pulling python:3.10-slim...")
        client.images.pull('python:3.10-slim')
        
        # Create container with long-running command
        print("📦 Creating container...")
        container = client.containers.run(
            'python:3.10-slim',
            command='sleep infinity',
            detach=True
        )
        
        print(f"✅ Container created: {container.id[:12]}")
        return True, container
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False, None


def step_3_3_copy_file(container):
    """Step 3.3 - Copy File via TAR"""
    print("\n" + "=" * 60)
    print("STEP 3.3 - Copy File via TAR Stream")
    print("=" * 60)
    
    try:
        # Create /app directory
        print("📁 Creating /app directory...")
        container.exec_run("mkdir -p /app")
        
        # Use inference.py from app directory
        local_file = "inference.py"
        if not os.path.exists(local_file):
            print(f"❌ File not found: {local_file}")
            return False
        
        # Read the file
        with open(local_file, 'rb') as f:
            file_data = f.read()
        
        file_name = os.path.basename(local_file)
        
        # Create in-memory TAR
        tar_stream = io.BytesIO()
        tar = tarfile.open(fileobj=tar_stream, mode='w')
        
        tarinfo = tarfile.TarInfo(name=file_name)
        tarinfo.size = len(file_data)
        tarinfo.mode = 0o755
        
        tar.addfile(tarinfo, io.BytesIO(file_data))
        tar.close()
        
        tar_stream.seek(0)
        tar_data = tar_stream.read()
        
        # Copy to container
        print(f"📤 Copying {file_name} to /app...")
        success = container.put_archive("/app", tar_data)
        
        if success:
            # Verify
            exit_code, output = container.exec_run("ls -lh /app/")
            print(f"✅ File copied successfully!")
            print(f"   Files in /app:\n{output.decode('utf-8')}")
            return True
        else:
            print("❌ Copy failed")
            return False
            
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False


def step_3_4_exec_command(container):
    """Step 3.4 - Execute Command"""
    print("\n" + "=" * 60)
    print("STEP 3.4 - Execute Command")
    print("=" * 60)
    
    try:
        print("▶️  Running: python --version")
        exit_code, output = container.exec_run("python --version")
        
        version = output.decode('utf-8').strip()
        print(f"✅ Output: {version}")
        print(f"   Exit code: {exit_code}")
        
        return exit_code == 0
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False


def step_3_5_commit_image(container):
    """Step 3.5 - Commit Container to Image"""
    print("\n" + "=" * 60)
    print("STEP 3.5 - Commit Container to Image")
    print("=" * 60)
    
    try:
        image_name = "my-python-model"
        image_tag = "v1.0"
        
        print(f"📸 Committing container to {image_name}:{image_tag}...")
        new_image = container.commit(
            repository=image_name,
            tag=image_tag,
            message="Built without Dockerfile"
        )
        
        print(f"✅ Image created!")
        print(f"   Image ID: {new_image.id[:12]}")
        print(f"   Tags: {new_image.tags}")
        
        return True, new_image
    except Exception as e:
        print(f"❌ Failed: {e}")
        return False, None


def cleanup(container):
    """Clean up container"""
    print("\n" + "=" * 60)
    print("CLEANUP")
    print("=" * 60)
    
    try:
        print("🧹 Stopping and removing container...")
        container.stop()
        container.remove()
        print("✅ Cleanup complete")
    except Exception as e:
        print(f"⚠️  Cleanup warning: {e}")


def main():
    print("\n🚀 DOCKER SDK VERIFICATION - ALL STEPS\n")
    
    results = []
    container = None
    
    # Step 3.1
    success, client = step_3_1_docker_connection()
    results.append(("3.1 - Docker Connection", success))
    if not success:
        print("\n❌ Cannot proceed without Docker connection")
        return
    
    # Step 3.2
    success, container = step_3_2_create_container(client)
    results.append(("3.2 - Create Container", success))
    if not success or container is None:
        print("\n❌ Cannot proceed without container")
        return
    
    # Step 3.3
    success = step_3_3_copy_file(container)
    results.append(("3.3 - Copy File (TAR)", success))
    
    # Step 3.4
    success = step_3_4_exec_command(container)
    results.append(("3.4 - Execute Command", success))
    
    # Step 3.5
    success, image = step_3_5_commit_image(container)
    results.append(("3.5 - Commit Image", success))
    
    # Cleanup
    if container:
        cleanup(container)
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY REPORT")
    print("=" * 60)
    
    all_passed = True
    for step, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {step}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 ALL STEPS PASSED!")
        print("\n🏆 MILESTONE ACHIEVED:")
        print("   ✓ Built an image without Dockerfile")
        print("   ✓ Copied files via TAR streams")
        print("   ✓ Executed commands in container")
        print("   ✓ Committed container to image")
    else:
        print("❌ SOME STEPS FAILED - Review output above")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
