import requests
import subprocess
import socket
import time

from dataclasses import dataclass, asdict
from typing import TypedDict


class APIAuthCredentials(TypedDict):
    username: str
    email: str
    password: str

class FastAPIServer:
    def __init__(self, app_module: str, port: int = 8000, verbose: bool = False):
        self.app_module = app_module
        self.port = port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process = None

        self.session = requests.Session()
        self.verbose = verbose

    def __enter__(self):
        """Starts the FastAPI server when entering the 'with' block."""
        print(f"[+] Starting FastAPI server ({self.app_module}) on port {self.port}...")
        
        self.process = subprocess.Popen(
            ["uv", "run", "uvicorn", self.app_module, "--port", str(self.port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait for the port to actively open
        start_time = time.time()
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(('127.0.0.1', self.port)) == 0:
                    break  # Server is up!
            if time.time() - start_time > 5:
                self.__exit__(None, None, None)
                raise TimeoutError("FastAPI server failed to start within 5 seconds.")
            time.sleep(0.1)
            
        print("[✓] Server is live and ready.")
        return self
    
    def authenticate(self, credentials: APIAuthCredentials = {"username": "testuser", "email": "testuser@gmail.com", "password": "testpassword"}) -> 'FastAPIServer':
        signup_credential: dict = {"username": credentials["username"], "email": credentials["email"], "password": credentials["password"]}
        try:
            signup_res = self.session.post(f"{self.base_url}/api/users/register", json=signup_credential)
            if signup_res.status_code == 201:
                print("[+] User registered successfully.")
            elif signup_res.status_code in {400, 409}:
                print("[!] User already exists, proceeding to login.")
            else:
                print(f"[!] Registration failed: {signup_res.status_code} - {signup_res.text}")
                signup_res.raise_for_status()
        except requests.RequestException as e:
            print(f"[!] Error during user registration: {e}")
            raise
        login_credential: dict = {"username": credentials["username"], "password": credentials["password"]}
        login_res = self.session.post(f"{self.base_url}/api/users/token", data=login_credential)
        login_res.raise_for_status()

        token = login_res.json().get("access_token")
        if not token:
            raise ValueError("Failed to retrieve access token from login response.")
        
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        print("[✓] Authentication successful, token set in session headers.")

        return self



    def __exit__(self, exc_type, exc_val, exc_tb):
        """Guarantees server shutdown when exiting the 'with' block."""
        if self.process:
            print("[+] Shutting down FastAPI server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()  # Hard kill if it refuses to exit
            print("[✓] Server stopped.")

    def list_conversion_jobs(self):
        """Fetches the list of conversion jobs from the API."""
        response = self.session.get(f"{self.base_url}/api/conversions/supported")
        response.raise_for_status()
        return response.json()
    
    def create_conversion_job(self, input_key: str, source_format: str, target_format: str):
        """Creates a new conversion job via the API."""
        payload = {
            "input_key": input_key,
            "source_format": source_format,
            "target_format": target_format
        }
        response = self.session.post(f"{self.base_url}/api/conversions/jobs", json=payload)
        response.raise_for_status()
        
        return response.json()
    
    def verify_upload_session(self, upload_id: str, job_id: str | None = None):
        """Verifies the completion of an upload session."""
        params = {"job_id": job_id} if job_id else {}
        response = self.session.post(f"{self.base_url}/api/uploads/sessions/{upload_id}/verify", params=params)
        response.raise_for_status()
        return response.json()