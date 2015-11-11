from setuptools import setup, find_packages
import os


def parse_requirements():
    path = os.path.join(os.path.dirname(__file__), "res", "scheduling",
                        "requirements", "base.txt")
    reqs = ["res-core==1.0.0"]
    with open(path, "r") as fin:
        for r in fin.read().split("\n"):
            r = r.strip()
            if r.startswith("#") or not r:
                continue
            if r.startswith("git+"):
                print("Warning: git dependencies cannot be used in setuptools "
                      "(%s)" % r)
                continue
            if not r.startswith("-r"):
                reqs.append(r)
    return reqs


setup(
    name="res-scheduling",
    description="RESystem Scheduling Service",
    version="1.0.0",
    license="Proprietary",
    author="Angry Developers",
    author_email="gmarkhor@gmail.com",
    url="http://dev.res-it.net/gerrit/#/admin/projects/res-scheduler",
    download_url='http://dev.res-it.net/gerrit/#/admin/projects/res-scheduler',
    packages=["res.scheduling"],
    install_requires=parse_requirements(),
    package_data={"": [
        'res/scheduling/requirements/base.txt',
        'res/scheduling/res_scheduling.service',
        'res/scheduling/run.sh']},
    classifiers=[
        "Development Status :: 5 - Stable",
        "Environment :: Console",
        "License :: Proprietary",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3.4"
    ]
)
