from setuptools import setup, find_packages

setup(
    name="ps01-loan-memory",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "fastapi==0.104.1",
        "uvicorn==0.24.0",
        "spacy==3.7.2",
        "mem0ai==0.0.15",
        "ollama==0.1.34",
        "docker==7.0.0",
        "python-dotenv==1.0.0",
        "pytest==7.4.3",
        "pytest-asyncio==0.21.1",
        "pydantic==2.5.0",
    ],
)
