from setuptools import find_packages, setup

setup(
    name="ui24r_client",
    description="Ui24R client",
    author="Jonathan Slenders",
    author_email="jonathan@slenders.be",
    version="0.1",
    license_files=["LICENSE"],
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=["anyio", "aiohttp"],
)
