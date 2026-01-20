"""
Step 3.4 - Run ONE Command Inside the Container

Goal: Execute python --version inside the container
Reference: https://docker-py.readthedocs.io/en/stable/containers.html#docker.models.containers.Container.exec_run
"""

import docker

def main():
    # Initialize Docker client
    client = docker.from_env()
    
    # Get the most recent container
    containers = client.containers.list(all=True, limit=1)
    
    if not containers:
        print("❌ No container found. Run builder.py first to create one.")
        return
    
    container = containers[0]
    print(f"📦 Using container: {container.id[:12]}")
    
    # Start the container if it's not running
    if container.status != 'running':
        print("🚀 Starting container...")
        # Recreate with a long-running command
        try:
            container.stop(timeout=1)
        except:
            pass
        
        container.remove()
        container = client.containers.run(
            'python:3.10-slim',
            command='sleep infinity',
            detach=True
        )
        print(f"   New container ID: {container.id[:12]}")
    
    # Execute python --version inside the container
    print("\n▶️  Running: python --version")
    exit_code, output = container.exec_run("python --version")
    
    # Decode and print the output
    version_output = output.decode('utf-8').strip()
    print(f"📤 Output: {version_output}")
    print(f"📊 Exit code: {exit_code}")
    
    if exit_code == 0:
        print("\n🎉 YOU WON! Successfully executed command inside container!")
    else:
        print(f"\n❌ Command failed with exit code {exit_code}")
    
    # Clean up
    print("\n🧹 Stopping container...")
    container.stop()

if __name__ == "__main__":
    main()
