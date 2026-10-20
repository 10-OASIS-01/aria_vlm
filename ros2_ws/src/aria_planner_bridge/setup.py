from glob import glob

from setuptools import find_packages, setup

name = "aria_planner_bridge"
setup(
    name=name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{name}"]),
        (f"share/{name}", ["package.xml"]),
        (f"share/{name}/launch", glob("launch/*.py")),
        (f"share/{name}/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    entry_points={"console_scripts": ["planner_bridge = aria_planner_bridge.node:main"]},
)
