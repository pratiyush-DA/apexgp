from setuptools import setup, find_packages

setup(
    name="apexgraph",
    version="1.0.0",
    packages=find_packages(include=["apps*", "packages*"], exclude=["tests*", "data*", "Workflow_Documentation*", "output_benchmark_*", "benchmarks*"]),
)
