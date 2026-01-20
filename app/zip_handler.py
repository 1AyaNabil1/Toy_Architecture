import io
import os
import tarfile
import docker


def copy_file_to_container(container, local_file_path, container_path="/app"):
    
    # Read the local file
    with open(local_file_path, 'rb') as f:
        file_data = f.read()
    
    # Get just the filename (not the full path)
    file_name = os.path.basename(local_file_path)
    
    # Create an in-memory TAR archive
    tar_stream = io.BytesIO()
    tar = tarfile.open(fileobj=tar_stream, mode='w')
    
    # Create a TarInfo object for the file
    tarinfo = tarfile.TarInfo(name=file_name)
    tarinfo.size = len(file_data)
    tarinfo.mode = 0o755  # Make it executable
    
    # Add the file to the TAR archive
    tar.addfile(tarinfo, io.BytesIO(file_data))
    tar.close()
    
    # Get the TAR data and rewind the stream
    tar_stream.seek(0)
    tar_data = tar_stream.read()
    
    # Use put_archive to copy the TAR into the container
    # This will extract the TAR contents into container_path
    success = container.put_archive(container_path, tar_data)
    
    return success


def main():
    """Main function to demonstrate copying a file into a container."""
    
    # Initialize Docker client
    client = docker.from_env()
    
    # Get the container (assumes builder.py created one)
    containers = client.containers.list(all=True, limit=1)
    
    if not containers:
        print("❌ No container found. Run builder.py first to create one.")
        return
    
    container = containers[0]
    print(f"📦 Using container: {container.id[:12]}")
    
    # Start the container (put_archive requires a running container)
    if container.status != 'running':
        print("🚀 Starting container...")
        # Need to give the container a command to keep it running
        # Let's stop it first and recreate with a long-running command
        try:
            container.stop(timeout=1)
        except:
            pass
        
        container.remove()
        print("📦 Creating new container with long-running command...")
        container = client.containers.run(
            'python:3.10-slim',
            command='sleep infinity',
            detach=True
        )
        import time
        time.sleep(2)  # Give container time to start
        print(f"   Container ID: {container.id[:12]}")
    
    # Create /app directory in the container
    print("📁 Creating /app directory in container...")
    container.exec_run("mkdir -p /app")
    
    # Path to the local file we want to copy
    local_file = "../sample_model/inference.py"
    
    if not os.path.exists(local_file):
        print(f"❌ File not found: {local_file}")
        return
    
    # Copy the file into the container
    print(f"📤 Copying {local_file} to container:/app/...")
    success = copy_file_to_container(container, local_file, container_path="/app")
    
    if success:
        print("✅ File copied successfully!")
        
        # Verify the file exists in the container
        print("\n🔍 Verifying file in container...")
        exit_code, output = container.exec_run("ls -lh /app/")
        print(output.decode('utf-8'))
        
        # Try to run the file
        print("\n▶️  Running inference.py in container...")
        exit_code, output = container.exec_run("python /app/inference.py")
        print(output.decode('utf-8'))
        
        if exit_code == 0:
            print("\n🎉 YOU WIN! File successfully landed in the container and executed!")
        else:
            print(f"\n⚠️  File exists but execution failed with exit code {exit_code}")
    else:
        print("❌ Failed to copy file")
    
    # Clean up
    print("\n🧹 Stopping container...")
    container.stop()


if __name__ == "__main__":
    main()
